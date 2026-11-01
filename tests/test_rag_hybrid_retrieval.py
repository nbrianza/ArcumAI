# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
"""Acceptance tests for issue #71: distinct hybrid results, small-archive
retrieval and a BM25 fail-safe.

The tests follow the approved design on the issue (owner decisions D1 to D7):

- ``src.utils.get_all_nodes_from_chroma`` rebuilds nodes exactly as the vector
  store does, so a chunk has the same id, text, metadata and hash in BM25 and
  in vector results, and the fusion step removes the duplicate (D1);
- ``src.utils.rebuild_bm25_index(chroma_collection, bm25_path) -> int`` holds
  the BM25 rebuild and returns the number of chunks indexed (0: nothing
  written); ``ingest.py`` calls it after an ingestion that archived a file
  (AC6) and on ``ingest.py --rebuild-bm25`` (AC5, same lock, no inbox scan);
- ``build_rag_retriever`` clamps the BM25 top k to the indexed chunk count and
  wraps BM25 in a fail-safe that logs only the exception class name and
  returns no BM25 results; the #45 owner filter stays the outermost layer;
- the BM25 load warning logs only the exception class name (D3).

Everything is synthetic and lives under ``tmp_path``. The fixtures reuse the
style of ``tests/test_rag_owner_filter.py``: a temporary Chroma collection and
BM25 index, a synthetic users file, ``MockEmbedding`` and ``MockLLM`` (which
echoes its prompt). The real index, archive, inbox, logs and users file are
never read or written.
"""
import asyncio
import importlib
import logging
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.test_rag_owner_filter import (
    ALPHA, BETA, ROOT, GAMMA, DELTA, NOPATH, ALL_MARKERS, CORE_DOCS, NAME_TOKENS,
    QUERY, TOP_VECTOR, USERS, _Env, _doc_text, _markers_in, _node_markers,
)

TECHNICAL_ERROR = "Sorry, a technical error occurred while processing the request."

# Raw Chroma metadata keys that must never reach a retrieved node (issue #71).
INTERNAL_KEYS = {"_node_content", "_node_type", "doc_id", "document_id", "ref_doc_id"}

# Three-chunk archive, smaller than RETRIEVER_TOP_K (AC7, AC8).
SMALL_DOCS = (
    (ALPHA, "alice\\alpha-note.txt"),
    (BETA, "bob\\beta-note.txt"),
    (ROOT, "root-note.txt"),
)

MATCH_PHRASE = "quarterly synthetic review"

# Tokens planted in a synthetic BM25 exception; none may reach a log record.
SECRET_MARKER = "SECRETMARK-3321"
SECRET_PATH = "C:\\synthetic-private\\clients\\secret-note.txt"


# --- Helpers -------------------------------------------------------------------

def _top_k() -> int:
    from src.config import RETRIEVER_TOP_K
    return RETRIEVER_TOP_K


def _ids(nodes) -> list[str]:
    return [n.node.node_id for n in nodes]


def _assert_no_duplicate_ids(nodes):
    ids = _ids(nodes)
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    assert not dupes, (f"{len(ids)} results hold only {len(set(ids))} distinct chunks; "
                       f"{len(dupes)} chunk ids appear more than once")


def _matching_extra(count: int, folder: str = "zz-matching"):
    """Chunks that match the query in both stores (top vector, query words)."""
    return [(f"Synthetic matching chunk {k} about the {MATCH_PHRASE}.",
             f"{folder}\\match-{k}.txt", TOP_VECTOR) for k in range(count)]


def _retrieve(username, role, query=QUERY):
    from src.ai.engines import build_rag_retriever
    return build_rag_retriever(username, role).retrieve(query)


def _aretrieve(username, role, query=QUERY):
    from src.ai.engines import build_rag_retriever
    return asyncio.run(build_rag_retriever(username, role).aretrieve(query))


def _legacy_bm25(collection, bm25_path: Path):
    """Writes a BM25 index the way main did before #71: raw Chroma metadata
    (including _node_content and the id keys) copied into each node."""
    from llama_index.core.schema import TextNode
    from llama_index.retrievers.bm25 import BM25Retriever
    data = collection.get(include=["documents", "metadatas"])
    nodes = [TextNode(text=t, metadata=m, id_=i)
             for i, t, m in zip(data["ids"], data["documents"], data["metadatas"])]
    BM25Retriever.from_defaults(nodes=nodes, similarity_top_k=5,
                                language="italian").persist(str(bm25_path))


def _vector_store_hashes(collection) -> dict:
    from llama_index.core import VectorStoreIndex
    from llama_index.vector_stores.chroma import ChromaVectorStore
    nodes = VectorStoreIndex.from_vector_store(
        ChromaVectorStore(chroma_collection=collection)
    ).as_retriever(similarity_top_k=collection.count()).retrieve(QUERY)
    return {n.node.node_id: n.node.hash for n in nodes}


def _assert_persisted_bm25_hashes_match_vector_store(env):
    from llama_index.retrievers.bm25 import BM25Retriever
    collection = env._collection()
    total = collection.count()
    loaded = BM25Retriever.from_persist_dir(str(env.bm25_path))
    loaded.similarity_top_k = total
    bm25_hash = {n.node.node_id: n.node.hash for n in loaded.retrieve(QUERY)}
    vector_hash = _vector_store_hashes(collection)
    assert set(bm25_hash) == set(vector_hash)
    differing = [i for i in bm25_hash if bm25_hash[i] != vector_hash[i]]
    assert not differing, f"{len(differing)} of {total} persisted BM25 chunks hash differently"


def _assert_messages_private(messages, extra_tokens=()):
    for message in messages:
        lowered = message.lower()
        for token in NAME_TOKENS:
            assert token not in lowered, "log record contains a synthetic file name"
        for marker in ALL_MARKERS:
            assert marker not in message, "log record contains chunk text or query text"
        for token in extra_tokens:
            assert token.lower() not in lowered, "log record contains a private token"


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = _Env(tmp_path, monkeypatch)
    yield e
    e.close()


# --- D1: BM25 nodes are built like vector store nodes -------------------------

def test_d1_nodes_from_chroma_carry_no_internal_metadata(env):
    collection = env.index()
    from src.utils import get_all_nodes_from_chroma
    nodes = get_all_nodes_from_chroma(collection)
    assert len(nodes) == collection.count()
    for node in nodes:
        leaked = INTERNAL_KEYS & set(node.metadata)
        assert not leaked, f"BM25 node metadata holds raw Chroma keys {sorted(leaked)}"


def test_d1_nodes_from_chroma_hash_like_vector_store_nodes(env):
    collection = env.index()
    from llama_index.core import VectorStoreIndex
    from llama_index.vector_stores.chroma import ChromaVectorStore
    from src.utils import get_all_nodes_from_chroma

    total = collection.count()
    vector_nodes = VectorStoreIndex.from_vector_store(
        ChromaVectorStore(chroma_collection=collection)
    ).as_retriever(similarity_top_k=total).retrieve(QUERY)
    vector_hash = {n.node.node_id: n.node.hash for n in vector_nodes}
    bm25_hash = {n.node_id: n.hash for n in get_all_nodes_from_chroma(collection)}

    assert set(bm25_hash) == set(vector_hash)
    differing = [i for i in bm25_hash if bm25_hash[i] != vector_hash[i]]
    assert not differing, f"{len(differing)} of {total} chunks hash differently in BM25"


def test_d1_get_all_nodes_from_empty_collection_is_empty(env):
    collection = env.index(docs=(), with_bm25=False, pad=False)
    from src.utils import get_all_nodes_from_chroma
    assert get_all_nodes_from_chroma(collection) == []


# --- AC1: ADMIN hybrid retrieval has no duplicate chunks -----------------------

def test_ac1_admin_hybrid_retrieval_has_no_duplicate_chunks(env):
    env.index()
    _assert_no_duplicate_ids(_retrieve("admin1", "ADMIN"))


def test_ac1_admin_hybrid_async_retrieval_has_no_duplicate_chunks(env):
    env.index()
    _assert_no_duplicate_ids(_aretrieve("admin1", "ADMIN"))


def test_ac1_admin_engine_sources_have_no_duplicate_chunks(env):
    env.index()
    from src.ai.engines import load_rag_engine
    response = load_rag_engine("ADMIN", username="admin1").chat(QUERY)
    _assert_no_duplicate_ids(response.source_nodes)


def test_ac1_hybrid_results_carry_no_internal_metadata(env):
    # The copy that wins fusion reaches the LLM context with its metadata.
    env.index()
    for n in _retrieve("admin1", "ADMIN"):
        leaked = INTERNAL_KEYS & set(n.node.metadata)
        assert not leaked, f"retrieved node metadata holds raw Chroma keys {sorted(leaked)}"


# --- AC2: distinct chunk counts ------------------------------------------------

def test_ac2_at_least_top_k_matching_chunks_gives_exactly_top_k_distinct(env):
    k = _top_k()
    env.index(extra=_matching_extra(k + 2))
    nodes = _retrieve("admin1", "ADMIN")
    assert len(nodes) == k
    assert len(set(_ids(nodes))) == k, (
        f"{k} result slots hold only {len(set(_ids(nodes)))} distinct chunks")
    for n in nodes:
        assert MATCH_PHRASE in n.node.get_content(), "a non-matching chunk took a slot"


def test_ac2_fewer_matching_chunks_than_top_k_each_appears_once(env):
    # CORE_DOCS: six matching chunks; padding fills the rest of the archive.
    assert len(CORE_DOCS) < _top_k()
    env.index()
    nodes = _retrieve("admin1", "ADMIN")
    for marker in ALL_MARKERS:
        hits = [n for n in nodes if marker in n.node.get_content()]
        assert len(hits) == 1, f"matching chunk found {len(hits)} times, expected once"


def test_ac2_archive_smaller_than_top_k_returns_every_chunk_once(env):
    assert len(SMALL_DOCS) < _top_k()
    env.index(docs=SMALL_DOCS, pad=False)
    nodes = _retrieve("admin1", "ADMIN")
    assert len(nodes) == len(SMALL_DOCS)
    assert len(set(_ids(nodes))) == len(SMALL_DOCS)
    assert _node_markers(nodes) == {ALPHA, BETA, ROOT}


# --- AC3: the #45 [hybrid] case passes without its xfail mark -----------------

def test_ac3_owner_filter_hybrid_admin_case_has_no_xfail_mark():
    import tests.test_rag_owner_filter as owner_tests
    for mark in getattr(owner_tests.test_ac2_admin_retrieves_chunks_from_all_documents,
                        "pytestmark", []):
        if mark.name != "parametrize":
            continue
        for param in mark.args[1]:
            marks = getattr(param, "marks", ())
            assert not any(m.name == "xfail" for m in marks), (
                "remove the xfail(strict=True) mark referencing #71 (design D6)")


def test_ac3_admin_hybrid_retrieves_chunks_from_all_documents(env):
    # Same setup and assertion as test_ac2_admin_retrieves_chunks_from_all_documents[hybrid].
    env.index(with_bm25=True)
    found = _node_markers(_retrieve("admin1", "ADMIN"))
    assert {ALPHA, BETA, ROOT, GAMMA, DELTA, NOPATH} <= found


# --- AC4: non-admin hybrid retrieval, no duplicates, owner filter kept --------

def test_ac4_non_admin_hybrid_retrieval_has_no_duplicates_and_only_own_chunks(env):
    env.index()
    nodes = _retrieve("alice", "LEGAL")
    _assert_no_duplicate_ids(nodes)
    found = _node_markers(nodes)
    assert {ALPHA, GAMMA} <= found
    assert found <= {ALPHA, GAMMA}


def test_ac4_non_admin_with_many_own_chunks_gets_top_k_distinct_own_chunks(env):
    k = _top_k()
    env.index(extra=_matching_extra(k + 2, folder="alice"))
    nodes = _retrieve("alice", "LEGAL")
    _assert_no_duplicate_ids(nodes)
    assert len(nodes) == k
    for n in nodes:
        assert str(n.node.metadata.get("file_path", "")).lower().startswith("alice"), (
            "a chunk outside the user's folder reached a non-admin")


def test_ac4_non_admin_async_retrieval_has_no_duplicates_and_only_own_chunks(env):
    env.index()
    nodes = _aretrieve("alice", "LEGAL")
    _assert_no_duplicate_ids(nodes)
    assert _node_markers(nodes) <= {ALPHA, GAMMA}


# --- AC5: rebuilding an index built before the fix ----------------------------

def test_ac5_precondition_legacy_bm25_index_produces_duplicates(env):
    # Guards the fixture: the legacy index must reproduce defect A, otherwise
    # the upgrade tests below prove nothing.
    collection = env.index(with_bm25=False)
    _legacy_bm25(collection, env.bm25_path)
    ids = _ids(_retrieve("admin1", "ADMIN"))
    assert len(set(ids)) < len(ids)


def test_ac5_rebuild_helper_fixes_legacy_index(env):
    collection = env.index(with_bm25=False)
    _legacy_bm25(collection, env.bm25_path)
    from src.utils import rebuild_bm25_index
    count = rebuild_bm25_index(collection, env.bm25_path)
    assert count == collection.count()
    nodes = _retrieve("admin1", "ADMIN")
    _assert_no_duplicate_ids(nodes)
    assert len(nodes) == _top_k()
    assert {ALPHA, BETA, ROOT, GAMMA, DELTA, NOPATH} <= _node_markers(nodes)
    _assert_persisted_bm25_hashes_match_vector_store(env)


def test_ac5_rebuild_helper_persists_clean_metadata(env):
    collection = env.index(with_bm25=False)
    from llama_index.retrievers.bm25 import BM25Retriever
    from src.utils import rebuild_bm25_index
    rebuild_bm25_index(collection, env.bm25_path)
    loaded = BM25Retriever.from_persist_dir(str(env.bm25_path))
    loaded.similarity_top_k = 3
    nodes = loaded.retrieve(QUERY)
    assert nodes
    for n in nodes:
        leaked = INTERNAL_KEYS & set(n.node.metadata)
        assert not leaked, f"persisted BM25 node holds raw Chroma keys {sorted(leaked)}"


def test_ac5_rebuild_helper_on_empty_collection_writes_nothing(env):
    collection = env.index(docs=(), with_bm25=False, pad=False)
    from src.utils import rebuild_bm25_index
    assert rebuild_bm25_index(collection, env.bm25_path) == 0
    assert not (env.bm25_path / "retriever.json").exists()


def test_ac5_rebuild_helper_logs_no_chunk_text_or_names(env, monkeypatch):
    handler = env.handler
    monkeypatch.setattr(logging.getLogger("ArcumIngestion"), "handlers", [handler])
    collection = env.index(with_bm25=False)
    from src.utils import rebuild_bm25_index
    rebuild_bm25_index(collection, env.bm25_path)
    _assert_messages_private(env.messages(), extra_tokens=(str(env.tmp_path),))


# --- AC5 / AC6: ingest.py --------------------------------------------------------

def _get_ingest():
    """Import ingest.py with init_settings() mocked (as tests/test_ingest_pipeline.py)."""
    if "ingest" not in sys.modules:
        with patch("src.config.init_settings"):
            importlib.import_module("ingest")
    return sys.modules["ingest"]


class _Ingest:
    """ingest.py pointed at temporary folders, the synthetic Chroma and BM25
    of ``env``, a temporary lock file and an in-memory log handler."""

    def __init__(self, env, monkeypatch, argv):
        from llama_index.core import Settings
        from llama_index.core.node_parser import SentenceSplitter
        import src.config as config_module

        self.module = _get_ingest()
        self.env = env
        tmp = env.tmp_path
        self.inbox = tmp / "inbox_synthetic"
        self.archive = tmp / "archive_synthetic"
        self.lock = tmp / "ingestion_synthetic.lock"
        paths = {
            "INBOX_DIR": self.inbox,
            "ARCHIVE_DIR": self.archive,
            "ERROR_DIR": tmp / "errors_synthetic",
            "DUPLICATES_DIR": tmp / "duplicates_synthetic",
            "BM25_PATH": env.bm25_path,
            "DB_PATH": env.chroma_path,
            "CHROMA_PATH": env.chroma_path,
            "LOCK_FILE": self.lock,
        }
        for name, value in paths.items():
            monkeypatch.setattr(self.module, name, value, raising=False)
        monkeypatch.setattr(config_module, "DB_PATH", env.chroma_path, raising=False)
        self.inbox.mkdir(parents=True, exist_ok=True)

        if Settings.text_splitter is None:
            Settings.text_splitter = SentenceSplitter(chunk_size=512, chunk_overlap=64)
        monkeypatch.setattr(sys, "argv", list(argv))
        # Keep ingestion records out of logs/ingestion.log.
        monkeypatch.setattr(logging.getLogger("ArcumIngestion"), "handlers", [env.handler])

    def run(self):
        self.module.main()


def test_ac5_rebuild_option_rebuilds_legacy_index_without_inbox_files(env, monkeypatch, capsys):
    collection = env.index(with_bm25=False)
    _legacy_bm25(collection, env.bm25_path)
    run = _Ingest(env, monkeypatch, ["ingest.py", "--rebuild-bm25"])
    run.run()

    nodes = _retrieve("admin1", "ADMIN")
    _assert_no_duplicate_ids(nodes)
    assert len(nodes) == _top_k()
    for n in nodes:
        assert not INTERNAL_KEYS & set(n.node.metadata)
    _assert_persisted_bm25_hashes_match_vector_store(env)
    assert not run.lock.exists(), "lock file not released after the rebuild"


def test_ac5_rebuild_option_logs_chunk_count_only(env, monkeypatch, capsys):
    collection = env.index(with_bm25=False)
    _legacy_bm25(collection, env.bm25_path)
    _Ingest(env, monkeypatch, ["ingest.py", "--rebuild-bm25"]).run()

    messages = env.messages()
    count = str(collection.count())
    assert any("bm25" in m.lower() and count in m for m in messages), (
        "no log line states the number of chunks indexed")
    out = capsys.readouterr()
    _assert_messages_private(messages + [out.out, out.err],
                             extra_tokens=(str(env.tmp_path), "_node_content"))


def test_ac5_rebuild_option_does_not_process_the_inbox(env, monkeypatch):
    collection = env.index(with_bm25=False)
    _legacy_bm25(collection, env.bm25_path)
    run = _Ingest(env, monkeypatch, ["ingest.py", "--rebuild-bm25"])
    waiting = run.inbox / "zz-waiting" / "waiting-note.txt"
    waiting.parent.mkdir(parents=True)
    waiting.write_text("Synthetic waiting note for a later ingestion.", encoding="utf-8")
    before = collection.count()
    run.run()
    assert waiting.is_file(), "the rebuild option moved an inbox file"
    assert collection.count() == before, "the rebuild option ingested new chunks"
    _assert_no_duplicate_ids(_retrieve("admin1", "ADMIN"))


def test_ac5_rebuild_option_respects_ingestion_lock(env, monkeypatch):
    collection = env.index(with_bm25=False)
    _legacy_bm25(collection, env.bm25_path)
    before = {p.name: p.read_bytes() for p in env.bm25_path.iterdir() if p.is_file()}
    run = _Ingest(env, monkeypatch, ["ingest.py", "--rebuild-bm25"])
    run.lock.write_text("LOCKED by a synthetic concurrent run", encoding="utf-8")
    run.run()
    after = {p.name: p.read_bytes() for p in env.bm25_path.iterdir() if p.is_file()}
    assert after == before, "BM25 index written while another ingestion held the lock"
    assert run.lock.exists(), "the lock of the other run was removed"


def test_ac6_ingestion_that_archives_files_writes_deduplicated_bm25(env, monkeypatch):
    k = _top_k()
    env.index(docs=(), with_bm25=False, pad=False)  # empty collection
    run = _Ingest(env, monkeypatch, ["ingest.py"])
    folder = run.inbox / "zz-ingest-synthetic"
    folder.mkdir(parents=True)
    for i in range(k + 2):
        (folder / f"ingest-note-{i}.txt").write_text(
            f"Synthetic ingested note {i} about the {MATCH_PHRASE}.", encoding="utf-8")
    run.run()

    assert len(list(run.archive.rglob("*.txt"))) == k + 2, "ingestion did not archive"
    nodes = _retrieve("admin1", "ADMIN")
    _assert_no_duplicate_ids(nodes)
    assert len(nodes) == k
    for n in nodes:
        assert not INTERNAL_KEYS & set(n.node.metadata)
    # MockEmbedding ranks every chunk equally, so whether duplicates reach the
    # top k above depends on rank order. The deterministic condition for the
    # fusion to deduplicate is that every persisted BM25 node hashes like the
    # vector store node with the same id.
    _assert_persisted_bm25_hashes_match_vector_store(env)


# --- AC7: small archive answers through the web chat engine -------------------

def test_ac7_small_archive_sync_retrieval_does_not_raise(env):
    env.index(docs=SMALL_DOCS, pad=False)
    assert _node_markers(_retrieve("admin1", "ADMIN")) == {ALPHA, BETA, ROOT}


def test_ac7_small_archive_async_retrieval_does_not_raise(env):
    env.index(docs=SMALL_DOCS, pad=False)
    assert _node_markers(_aretrieve("admin1", "ADMIN")) == {ALPHA, BETA, ROOT}


def test_ac7_small_archive_non_admin_retrieval_does_not_raise(env):
    env.index(docs=SMALL_DOCS, pad=False)
    assert _node_markers(_retrieve("alice", "LEGAL")) == {ALPHA}
    assert _node_markers(_aretrieve("alice", "LEGAL")) == {ALPHA}


def test_ac7_small_archive_engine_chat_and_achat_answer(env):
    env.index(docs=SMALL_DOCS, pad=False)
    from src.ai.engines import load_rag_engine
    for engine_call in ("chat", "achat"):
        engine = load_rag_engine("ADMIN", username="admin1")
        if engine_call == "chat":
            response = engine.chat(QUERY)
        else:
            response = asyncio.run(engine.achat(QUERY))
        assert _node_markers(response.source_nodes) == {ALPHA, BETA, ROOT}
        assert {ALPHA, BETA, ROOT} <= _markers_in(str(response).replace(QUERY, ""))


@pytest.mark.parametrize("username,role,expected", [
    ("admin1", "ADMIN", {ALPHA, BETA, ROOT}),
    ("alice", "LEGAL", {ALPHA}),
])
def test_ac7_small_archive_session_answers_without_technical_error(env, username, role, expected):
    env.index(docs=SMALL_DOCS, pad=False)
    from src.ai.session import UserSession
    session = UserSession(username, role=role)
    response_obj, response_text, used_mode = asyncio.run(
        session.run_chat_action("@rag " + QUERY))
    assert used_mode == "RAG"
    assert TECHNICAL_ERROR not in response_text
    assert response_obj is not None, "engine raised; the user would see an error"
    assert _node_markers(response_obj.source_nodes) == expected


def test_ac7_empty_index_admin_answers_without_error(env):
    env.index(docs=(), with_bm25=False, pad=False)
    assert _retrieve("admin1", "ADMIN") == []
    from src.ai.session import UserSession
    session = UserSession("admin1", role="ADMIN")
    response_obj, response_text, _mode = asyncio.run(session.run_chat_action("@rag " + QUERY))
    assert TECHNICAL_ERROR not in response_text
    assert response_obj is not None


# --- AC8: small archive through the Outlook loopback --------------------------

def test_ac8_small_archive_loopback_reply_is_an_answer(env, monkeypatch):
    env.index(docs=SMALL_DOCS, pad=False)
    import src.engine as engine_module
    from src.bridge.loopback_processor import LoopbackProcessor

    async def _fake_optimize(subject, body):
        return QUERY

    monkeypatch.setattr(engine_module, "optimize_prompt_for_rag", _fake_optimize)
    processor = LoopbackProcessor({}, None)
    reply = asyncio.run(processor._route_to_ai_engine(
        USERS["admin1"]["outlook_id"], "Synthetic subject",
        "Synthetic body asking about the review", "", use_rag=True))

    assert TECHNICAL_ERROR not in reply
    # MockLLM echoes the prompt, so the reply carries the retrieved context.
    assert {ALPHA, BETA, ROOT} <= _markers_in(reply.replace(QUERY, ""))


# --- AC9: BM25 failure at query time falls back to vector retrieval ----------

def _break_bm25(monkeypatch):
    from llama_index.retrievers.bm25 import BM25Retriever

    def _fail(self, query_bundle):
        raise RuntimeError(f"synthetic failure {SECRET_MARKER} reading {SECRET_PATH} "
                           f"for query {query_bundle.query_str} near {_doc_text(ALPHA)}")

    monkeypatch.setattr(BM25Retriever, "_retrieve", _fail)


def _bm25_warnings(env):
    return [r for r in env.handler.records
            if r.levelno == logging.WARNING and "bm25" in r.getMessage().lower()]


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_ac9_bm25_query_failure_admin_answers_from_vector_results(env, monkeypatch, mode):
    env.index()
    _break_bm25(monkeypatch)
    nodes = _retrieve("admin1", "ADMIN") if mode == "sync" else _aretrieve("admin1", "ADMIN")
    assert {ALPHA, BETA, ROOT} <= _node_markers(nodes)
    assert len(_bm25_warnings(env)) == 1


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_ac9_bm25_query_failure_non_admin_keeps_owner_filter(env, monkeypatch, mode):
    env.index()
    _break_bm25(monkeypatch)
    nodes = _retrieve("alice", "LEGAL") if mode == "sync" else _aretrieve("alice", "LEGAL")
    found = _node_markers(nodes)
    assert ALPHA in found
    assert found <= {ALPHA, GAMMA}
    assert len(_bm25_warnings(env)) == 1


def test_ac9_bm25_query_failure_session_answers(env, monkeypatch):
    env.index()
    _break_bm25(monkeypatch)
    from src.ai.session import UserSession
    session = UserSession("admin1", role="ADMIN")
    response_obj, response_text, used_mode = asyncio.run(
        session.run_chat_action("@rag " + QUERY))
    assert used_mode == "RAG"
    assert TECHNICAL_ERROR not in response_text
    assert response_obj is not None
    assert {ALPHA, BETA, ROOT} <= _node_markers(response_obj.source_nodes)


def test_ac9_bm25_query_failure_warning_has_no_query_text_or_path(env, monkeypatch):
    env.index()
    _break_bm25(monkeypatch)
    from src.ai.engines import load_rag_engine
    load_rag_engine("ADMIN", username="admin1").chat(QUERY)

    warnings = _bm25_warnings(env)
    assert len(warnings) == 1
    assert "RuntimeError" in warnings[0].getMessage(), (
        "the warning should name the exception class (design point 5)")
    _assert_messages_private(
        env.messages(),
        extra_tokens=(SECRET_MARKER, SECRET_PATH, "synthetic-private", "synthetic failure",
                      MATCH_PHRASE))


# --- D3: BM25 load warning names only the exception class ---------------------

def test_d3_bm25_load_failure_warning_has_no_path(env):
    env.index(with_bm25=False)
    env.bm25_path.mkdir(parents=True, exist_ok=True)  # exists but holds no index
    nodes = _retrieve("admin1", "ADMIN")
    assert {ALPHA, BETA, ROOT} <= _node_markers(nodes), "vector-only fallback lost"
    warnings = [r.getMessage() for r in env.handler.records
                if r.levelno >= logging.WARNING and "bm25" in r.getMessage().lower()]
    assert warnings, "no warning about the BM25 load failure"
    _assert_messages_private(warnings, extra_tokens=(str(env.tmp_path), env.bm25_path.name,
                                                     "retriever.json"))
