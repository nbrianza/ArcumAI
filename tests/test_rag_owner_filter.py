# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
"""Acceptance tests for issue #45: RAG retrieval follows the document owner rule.

The tests follow the approved design on the issue (owner decisions D1 to D4):

- ``src.documents.indexed_path_owner(file_path)`` returns the owner of a chunk
  from the ``file_path`` recorded at ingestion (first folder, otherwise None);
  the decision itself stays in ``can_read`` (AC8);
- ``src.ai.engines.build_rag_retriever(username, role)`` builds the hybrid
  retriever; ADMIN gets the unfiltered retriever, any other role only chunks
  whose indexed owner matches the username case-insensitively (AC1 to AC4,
  AC6). The filter is applied before ranking (Chroma ``in`` filter, BM25
  weight mask) so a non-admin keeps full recall on their own chunks (D1), and
  a post-filter on every sub-retriever result is the security guarantee;
- ``load_rag_engine(user_role, username=None)``: a missing username for a
  non-admin role means "nothing readable", never "no filter";
- ``UserSession.get_rag_engine`` re-reads the role from the users file and
  passes the username (AC7, D3); the Outlook loopback path builds a
  ``UserSession`` per request, so it inherits the filter (AC5).

Everything is synthetic and lives under ``tmp_path``: a temporary Chroma
collection and BM25 index built the same way as ``ingest.py`` does, a
synthetic users file, ``MockEmbedding`` and ``MockLLM`` from llama-index.
``MockLLM`` echoes its prompt, so the text of a chat response is the context
that reached the LLM. The real index, archive and users file are never read.
"""
import asyncio
import json
import logging
from pathlib import Path

import pytest

# --- Synthetic data ----------------------------------------------------------

ALPHA = "ALPHA-7731"     # alice\alpha-note.txt
BETA = "BETA-4410"       # bob\beta-note.txt
ROOT = "ROOT-9052"       # root-note.txt (archive root, no owner)
GAMMA = "GAMMA-5126"     # ALICE\gamma-note.txt (owner folder in other case)
DELTA = "DELTA-8803"     # stray\delta-note.txt (folder matching no user)
NOPATH = "NOPATH-1188"   # chunk without file_path metadata

ALL_MARKERS = (ALPHA, BETA, ROOT, GAMMA, DELTA, NOPATH)

# (marker, file_path as written by ingest.py on Windows, or None)
CORE_DOCS = (
    (ALPHA, "alice\\alpha-note.txt"),
    (BETA, "bob\\beta-note.txt"),
    (ROOT, "root-note.txt"),
    (GAMMA, "ALICE\\gamma-note.txt"),
    (DELTA, "stray\\delta-note.txt"),
    (NOPATH, None),
)

# Tokens from synthetic file and folder names; none may appear in a log record.
NAME_TOKENS = ("alpha-note", "beta-note", "root-note", "gamma-note",
               "delta-note", "stray", "beta-bulk")

QUERY = ("quarterly synthetic review " + " ".join(ALL_MARKERS))

EMBED_DIM = 8
TOP_VECTOR = [0.5] * EMBED_DIM            # equals the MockEmbedding query vector
FAR_VECTOR = [0.5] * 4 + [0.05] * 4       # less similar to the query

USERS = {
    "alice": {"pw_hash": "synthetic-not-a-hash", "role": "LEGAL",
              "name": "Alice Example", "outlook_id": "alice-outlook-synthetic"},
    "bob": {"pw_hash": "synthetic-not-a-hash", "role": "DEFAULT",
            "name": "Bob Example", "outlook_id": "bob-outlook-synthetic"},
    "carol": {"pw_hash": "synthetic-not-a-hash", "role": "EXECUTIVE",
              "name": "Carol Example", "outlook_id": ""},
    "admin1": {"pw_hash": "synthetic-not-a-hash", "role": "ADMIN",
               "name": "Admin Example", "outlook_id": "admin1-outlook-synthetic"},
}


def _doc_text(marker: str) -> str:
    return (f"Synthetic note {marker} about the quarterly synthetic review. "
            f"Reference code {marker}.")


def _markers_in(text: str) -> set[str]:
    return {m for m in ALL_MARKERS if m in text}


def _node_markers(nodes) -> set[str]:
    found = set()
    for n in nodes:
        found |= _markers_in(n.node.get_content())
    return found


# --- Log capture -------------------------------------------------------------

class _ListHandler(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


# --- Environment fixture -----------------------------------------------------

class _Env:
    """Synthetic Chroma + BM25 index, users file and mock models for one test."""

    def __init__(self, tmp_path: Path, monkeypatch):
        from llama_index.core import Settings
        from llama_index.core.embeddings import MockEmbedding
        from llama_index.core.llms import MockLLM
        import src.auth as auth_module
        import src.config as config_module
        import src.ai.engines as engines

        self.tmp_path = tmp_path
        self.monkeypatch = monkeypatch
        self.engines = engines
        self.chroma_path = tmp_path / "chroma_synthetic"
        self.bm25_path = tmp_path / "bm25_synthetic"

        # Mock models: no network, no Ollama, no HuggingFace download.
        monkeypatch.setattr(Settings, "_embed_model", MockEmbedding(embed_dim=EMBED_DIM))
        monkeypatch.setattr(Settings, "_llm", MockLLM())

        # Index locations used by the engine.
        monkeypatch.setattr(engines, "CHROMA_PATH", self.chroma_path)
        monkeypatch.setattr(engines, "BM25_PATH", self.bm25_path)
        monkeypatch.setattr(config_module, "CHROMA_PATH", self.chroma_path)
        monkeypatch.setattr(config_module, "BM25_PATH", self.bm25_path)

        # Users file: never read the real users.json.
        self.users_file = tmp_path / "users_synthetic.json"
        self.write_users(USERS)
        monkeypatch.setattr(auth_module, "USERS_FILE", self.users_file)
        monkeypatch.setattr(config_module, "USERS_FILE", self.users_file, raising=False)

        # Log capture (ArcumServer has propagate = False).
        self.handler = _ListHandler()
        server_logger = logging.getLogger("ArcumServer")
        monkeypatch.setattr(server_logger, "handlers", [self.handler])
        root_logger = logging.getLogger()
        root_logger.addHandler(self.handler)
        self._root_logger = root_logger

    def close(self):
        self._root_logger.removeHandler(self.handler)

    def write_users(self, users: dict):
        self.users_file.write_text(json.dumps(users), encoding="utf-8")

    def _collection(self):
        import chromadb
        from src.config import COLLECTION_NAME
        client = chromadb.PersistentClient(path=str(self.chroma_path))
        return client.get_or_create_collection(COLLECTION_NAME)

    def index(self, docs=CORE_DOCS, extra=(), with_bm25=True, pad=True):
        """Writes chunks to Chroma and rebuilds BM25 like ingest.py does.

        docs: (marker, file_path) pairs with the top similarity vector.
        extra: (text, file_path, embedding) triples for recall tests.
        pad: adds unrelated chunks under a folder that matches no user, so the
        BM25 corpus is larger than RETRIEVER_TOP_K (bm25s rejects a top-k
        larger than the corpus) without competing with the query.
        """
        from src.config import RETRIEVER_TOP_K
        if pad:
            extra = list(extra) + [
                (f"Unrelated padding paragraph number {k} lorem ipsum dolor.",
                 f"zz-padding\\padding-{k}.txt", FAR_VECTOR)
                for k in range(2 * RETRIEVER_TOP_K)]
        from llama_index.core.schema import TextNode
        from llama_index.vector_stores.chroma import ChromaVectorStore

        nodes = []
        for i, (marker, file_path) in enumerate(docs):
            meta = {"file_hash": f"synthetic-hash-{i}",
                    "filename": (file_path or "nopath-note.txt").split("\\")[-1]}
            if file_path is not None:
                meta["file_path"] = file_path
            nodes.append(TextNode(text=_doc_text(marker), metadata=meta,
                                  embedding=list(TOP_VECTOR)))
        for j, (text, file_path, embedding) in enumerate(extra):
            meta = {"file_hash": f"synthetic-extra-{j}",
                    "filename": file_path.split("\\")[-1],
                    "file_path": file_path}
            nodes.append(TextNode(text=text, metadata=meta, embedding=list(embedding)))

        collection = self._collection()
        if nodes:
            ChromaVectorStore(chroma_collection=collection).add(nodes)

        if with_bm25 and nodes:
            from llama_index.retrievers.bm25 import BM25Retriever
            from src.utils import get_all_nodes_from_chroma
            all_nodes = get_all_nodes_from_chroma(collection)
            bm25 = BM25Retriever.from_defaults(nodes=all_nodes, similarity_top_k=5,
                                               language="italian")
            bm25.persist(str(self.bm25_path))
        return collection

    def messages(self) -> list[str]:
        out = []
        for r in self.handler.records:
            try:
                out.append(r.getMessage())
            except Exception:
                out.append(str(r.msg))
        return out


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = _Env(tmp_path, monkeypatch)
    yield e
    e.close()


def _retrieve(username, role, query=QUERY):
    from src.ai.engines import build_rag_retriever
    retriever = build_rag_retriever(username, role)
    return retriever.retrieve(query)


# --- AC1: alice only gets her own chunks --------------------------------------

@pytest.mark.parametrize("with_bm25", [True, False], ids=["hybrid", "vector_only"])
def test_ac1_alice_retrieves_only_own_chunks(env, with_bm25):
    env.index(with_bm25=with_bm25)
    found = _node_markers(_retrieve("alice", "LEGAL"))
    assert ALPHA in found
    assert BETA not in found
    assert ROOT not in found
    assert found <= {ALPHA, GAMMA}


@pytest.mark.parametrize("with_bm25", [True, False], ids=["hybrid", "vector_only"])
def test_ac1_alice_answer_context_and_sources_only_own_chunks(env, with_bm25):
    env.index(with_bm25=with_bm25)
    from src.ai.engines import load_rag_engine
    engine = load_rag_engine("LEGAL", username="alice")
    response = engine.chat(QUERY)
    sources = _node_markers(response.source_nodes)
    # MockLLM echoes the prompt: the response text is the LLM context plus the
    # question, which itself names every marker and is removed first.
    context_markers = _markers_in(str(response).replace(QUERY, ""))
    assert ALPHA in sources
    assert sources <= {ALPHA, GAMMA}
    assert BETA not in context_markers
    assert ROOT not in context_markers
    assert ALPHA in context_markers


# --- AC2: admin retrieves from every document ---------------------------------

@pytest.mark.parametrize("with_bm25", [True, False], ids=["hybrid", "vector_only"])
def test_ac2_admin_retrieves_chunks_from_all_documents(env, with_bm25):
    env.index(with_bm25=with_bm25)
    found = _node_markers(_retrieve("admin1", "ADMIN"))
    assert {ALPHA, BETA, ROOT, GAMMA, DELTA, NOPATH} <= found


def test_ac2_admin_engine_without_username_is_unfiltered(env):
    env.index()
    from src.ai.engines import load_rag_engine
    response = load_rag_engine("ADMIN").chat(QUERY)
    assert {ALPHA, BETA, ROOT} <= _node_markers(response.source_nodes)


# --- AC3: case-insensitive owner folder ---------------------------------------

@pytest.mark.parametrize("with_bm25", [True, False], ids=["hybrid", "vector_only"])
def test_ac3_alice_gets_chunks_from_uppercase_folder(env, with_bm25):
    env.index(with_bm25=with_bm25)
    found = _node_markers(_retrieve("alice", "LEGAL"))
    assert GAMMA in found


def test_ac3_uppercase_username_matches_lowercase_folder(env):
    env.index()
    found = _node_markers(_retrieve("Alice", "LEGAL"))
    assert {ALPHA, GAMMA} <= found
    assert found <= {ALPHA, GAMMA}


# --- AC4: unknown folder, archive root and no file_path are admin-only --------

@pytest.mark.parametrize("username,role", [("alice", "LEGAL"), ("bob", "DEFAULT"),
                                           ("carol", "EXECUTIVE")])
@pytest.mark.parametrize("with_bm25", [True, False], ids=["hybrid", "vector_only"])
def test_ac4_unowned_chunks_never_reach_non_admin(env, username, role, with_bm25):
    env.index(with_bm25=with_bm25)
    found = _node_markers(_retrieve(username, role))
    assert ROOT not in found
    assert DELTA not in found
    assert NOPATH not in found


def test_ac4_user_named_like_stray_folder_owns_it_but_nothing_else(env):
    # Control: the folder rule, not a hard-coded list, decides ownership.
    env.index()
    found = _node_markers(_retrieve("stray", "DEFAULT"))
    assert found == {DELTA}


# --- AC5: Outlook loopback --------------------------------------------------

def test_ac5_loopback_answer_for_bob_built_only_from_bob_chunks(env, monkeypatch):
    env.index()
    import src.engine as engine_module
    from src.bridge.loopback_processor import LoopbackProcessor

    async def _fake_optimize(subject, body):
        return QUERY

    monkeypatch.setattr(engine_module, "optimize_prompt_for_rag", _fake_optimize)

    processor = LoopbackProcessor({}, None)
    response_text = asyncio.run(processor._route_to_ai_engine(
        USERS["bob"]["outlook_id"], "Synthetic subject", "Synthetic body asking about the review",
        "", use_rag=True))

    # MockLLM echoes the prompt, so the reply contains the retrieved context.
    assert BETA in response_text.replace(QUERY, "")
    leaked = _markers_in(response_text.replace(QUERY, "")) - {BETA}
    assert leaked == set()


# --- AC6: no matching documents ---------------------------------------------

@pytest.mark.parametrize("with_bm25", [True, False], ids=["hybrid", "vector_only"])
def test_ac6_user_without_documents_gets_empty_retrieval(env, with_bm25):
    env.index(with_bm25=with_bm25)
    assert _retrieve("carol", "EXECUTIVE") == []


def test_ac6_user_without_documents_gets_answer_with_empty_sources(env):
    env.index()
    from src.ai.engines import load_rag_engine
    response = load_rag_engine("EXECUTIVE", username="carol").chat(QUERY)
    assert list(response.source_nodes) == []
    assert _markers_in(str(response).replace(QUERY, "")) == set()


def test_ac6_non_admin_on_empty_index_gets_empty_retrieval(env):
    env.index(docs=(), with_bm25=False, pad=False)
    assert _retrieve("alice", "LEGAL") == []


def test_ac6_session_rag_question_without_documents_has_no_error(env):
    env.index()
    from src.ai.session import UserSession
    session = UserSession("carol", role="EXECUTIVE")
    response_obj, response_text, used_mode = asyncio.run(
        session.run_chat_action("@rag " + QUERY))
    assert used_mode == "RAG"
    assert response_obj is not None, "engine raised; the user would see an error"
    assert list(response_obj.source_nodes) == []
    assert _markers_in(response_text.replace(QUERY, "")) == set()


# --- AC7: role re-read from the user store ----------------------------------

def _session_rag(username, cached_role):
    from src.ai.session import UserSession
    session = UserSession(username, role=cached_role)
    response_obj, response_text, used_mode = asyncio.run(
        session.run_chat_action("@rag " + QUERY))
    assert used_mode == "RAG"
    assert response_obj is not None, "engine raised instead of answering"
    return response_obj, response_text


def test_ac7_promoted_user_gets_admin_retrieval_in_new_session(env):
    env.index()
    users = json.loads(json.dumps(USERS))
    users["alice"]["role"] = "ADMIN"
    env.write_users(users)
    response_obj, _ = _session_rag("alice", "LEGAL")  # role cached at login
    assert {ALPHA, BETA, ROOT} <= _node_markers(response_obj.source_nodes)


def test_ac7_demoted_admin_gets_filtered_retrieval_in_new_session(env):
    env.index()
    users = json.loads(json.dumps(USERS))
    users["admin1"]["role"] = "LEGAL"
    env.write_users(users)
    response_obj, response_text = _session_rag("admin1", "ADMIN")  # stale cached role
    assert _node_markers(response_obj.source_nodes) == set()
    assert _markers_in(response_text.replace(QUERY, "")) == set()


def test_ac7_user_removed_from_store_gets_nothing(env):
    env.index()
    users = json.loads(json.dumps(USERS))
    del users["admin1"]
    env.write_users(users)
    response_obj, response_text = _session_rag("admin1", "ADMIN")
    assert _node_markers(response_obj.source_nodes) == set()
    assert _markers_in(response_text.replace(QUERY, "")) == set()


def test_ac7_session_passes_username_so_non_admin_is_filtered(env):
    env.index()
    response_obj, _ = _session_rag("alice", "LEGAL")
    found = _node_markers(response_obj.source_nodes)
    assert ALPHA in found
    assert found <= {ALPHA, GAMMA}


# --- AC8: retrieval and download rules agree --------------------------------

AC8_PATHS = [
    "alice\\alpha-note.txt",
    "alice/alpha-note.txt",
    "alice\\sub\\deeper\\nested-note.txt",
    "alice/sub/deeper/nested-note.txt",
    "ALICE\\gamma-note.txt",
    "Alice/mixed-case-note.txt",
    "bob\\beta-note.txt",
    "BOB/sub/beta-nested.txt",
    "admin1\\admin-note.txt",
    "stray\\delta-note.txt",
    "nobody/sub/unknown-note.txt",
    "root-note.txt",
    "alice",
]
AC8_USERS = [("alice", "LEGAL"), ("bob", "DEFAULT"), ("admin1", "ADMIN")]


@pytest.mark.parametrize("username,role", AC8_USERS)
@pytest.mark.parametrize("rel_path", AC8_PATHS)
def test_ac8_retrieval_rule_matches_download_rule(tmp_path, username, role, rel_path):
    from src.documents import (can_read, document_owner, indexed_path_owner,
                               resolve_archive_path)
    root = tmp_path / "archive"
    root.mkdir()
    resolved = resolve_archive_path(rel_path, root)
    assert resolved is not None, "synthetic case must be a valid archive path"
    download = can_read(username, role, document_owner(resolved, root))
    retrieval = can_read(username, role, indexed_path_owner(rel_path))
    assert retrieval == download


@pytest.mark.parametrize("file_path", [
    None, "", "root-note.txt", "C:\\elsewhere\\alice\\x.txt", "\\alice\\x.txt",
    "/alice/x.txt", "..\\alice\\x.txt", "../alice/x.txt",
])
def test_ac8_indexed_path_without_owner_is_none(file_path):
    from src.documents import indexed_path_owner
    assert indexed_path_owner(file_path) is None


@pytest.mark.parametrize("file_path,owner", [
    ("alice\\alpha-note.txt", "alice"),
    ("alice/alpha-note.txt", "alice"),
    ("ALICE\\sub\\gamma-note.txt", "ALICE"),
])
def test_ac8_indexed_path_owner_is_first_folder(file_path, owner):
    from src.documents import indexed_path_owner
    assert indexed_path_owner(file_path) == owner


# --- Design: deny by default when the username is missing --------------------

def test_design_non_admin_engine_without_username_reads_nothing(env):
    env.index()
    from src.ai.engines import load_rag_engine
    response = load_rag_engine("LEGAL").chat(QUERY)
    assert _node_markers(response.source_nodes) == set()


# --- Design D1: filter before ranking keeps recall on own chunks -------------

@pytest.mark.parametrize("with_bm25", [True, False], ids=["hybrid", "vector_only"])
def test_design_filter_before_ranking_keeps_own_chunk_when_others_rank_higher(env, with_bm25):
    from src.config import RETRIEVER_TOP_K
    # Many bob chunks that rank above alice's single chunk in both stores.
    bulk_text = ("quarterly synthetic review quarterly synthetic review "
                 "quarterly synthetic review " + BETA)
    extra = [(bulk_text, f"bob\\beta-bulk-{i}.txt", TOP_VECTOR)
             for i in range(3 * RETRIEVER_TOP_K)]
    extra.append(("Synthetic note " + ALPHA + " about the review.",
                  "alice\\alpha-far.txt", FAR_VECTOR))
    env.index(docs=(), extra=extra, with_bm25=with_bm25)
    found = _node_markers(_retrieve("alice", "LEGAL", "quarterly synthetic review"))
    assert found == {ALPHA}


# --- Design: post-filter is the guarantee even if a store ignores the filter --

def test_design_post_filter_drops_disallowed_nodes_returned_by_sub_retrievers(env, monkeypatch):
    env.index()
    from llama_index.core.indices.vector_store.retrievers import VectorIndexRetriever
    from llama_index.core.schema import NodeWithScore, TextNode
    from llama_index.retrievers.bm25 import BM25Retriever

    def _all_nodes():
        out = []
        for i, (marker, file_path) in enumerate(CORE_DOCS):
            meta = {"file_path": file_path} if file_path else {}
            out.append(NodeWithScore(node=TextNode(text=_doc_text(marker), metadata=meta,
                                                   id_=f"leak-{i}"), score=0.0))
        return out

    def _leaky(self, query_bundle):
        return _all_nodes()

    async def _aleaky(self, query_bundle):
        return _all_nodes()

    # Simulate stores that ignore the Chroma filter and the BM25 mask.
    monkeypatch.setattr(VectorIndexRetriever, "_retrieve", _leaky)
    monkeypatch.setattr(VectorIndexRetriever, "_aretrieve", _aleaky)
    monkeypatch.setattr(BM25Retriever, "_retrieve", _leaky)

    found = _node_markers(_retrieve("alice", "LEGAL"))
    assert found <= {ALPHA, GAMMA}
    assert ALPHA in found


# --- Privacy: no paths, names or chunk text in logs --------------------------

def test_privacy_filtered_retrieval_logs_no_paths_names_or_text(env):
    env.index()
    from src.ai.engines import build_rag_retriever, load_rag_engine
    build_rag_retriever("alice", "LEGAL").retrieve(QUERY)
    build_rag_retriever("carol", "EXECUTIVE").retrieve(QUERY)
    load_rag_engine("LEGAL", username="alice").chat(QUERY)
    for message in env.messages():
        lowered = message.lower()
        for token in NAME_TOKENS:
            assert token not in lowered, "log record contains a synthetic file name"
        for marker in ALL_MARKERS:
            assert marker not in message, "log record contains chunk text"
