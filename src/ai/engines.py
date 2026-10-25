# Copyright (c) 2026 Nicolas Brianza
# Licensed under the MIT License. See LICENSE file in the project root.
import os
from typing import List

import chromadb
from llama_index.core import VectorStoreIndex, StorageContext, Settings
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.core.retrievers import BaseRetriever, QueryFusionRetriever
from llama_index.core.schema import NodeWithScore, QueryBundle
from llama_index.core.vector_stores import FilterOperator, MetadataFilter, MetadataFilters
from llama_index.retrievers.bm25 import BM25Retriever
from llama_index.core.memory import ChatMemoryBuffer
from llama_index.core.chat_engine import ContextChatEngine, SimpleChatEngine
from llama_index.llms.gemini import Gemini

from src.config import (
    DB_PATH, CHROMA_PATH, BM25_PATH, COLLECTION_NAME,
    RETRIEVER_TOP_K, DEFAULT_SYSTEM_PROMPT, CUSTOM_CONTEXT_TEMPLATE, ROLE_PROMPTS
)
from src.logger import server_log as slog


class OwnerFilteredRetriever(BaseRetriever):
    """Drops every node the user may not read (#45, security guarantee).

    The Chroma 'in' filter and the BM25 weight mask only preserve recall; this
    wrapper is what guarantees that no chunk of another owner, and no chunk
    without an owner, reaches a non-admin. Logs nothing about the nodes.
    """

    def __init__(self, inner: BaseRetriever, username: str | None, role: str):
        self._inner = inner
        self._username = username
        self._role = role
        super().__init__()

    def _allowed(self, nodes: List[NodeWithScore]) -> List[NodeWithScore]:
        from src.documents import can_read, indexed_path_owner
        return [n for n in nodes
                if can_read(self._username, self._role,
                            indexed_path_owner(n.node.metadata.get("file_path")))]

    def _retrieve(self, query_bundle: QueryBundle) -> List[NodeWithScore]:
        return self._allowed(self._inner.retrieve(query_bundle))

    async def _aretrieve(self, query_bundle: QueryBundle) -> List[NodeWithScore]:
        return self._allowed(await self._inner.aretrieve(query_bundle))


class _EmptyRetriever(BaseRetriever):
    """Retriever for a non-admin user who may read no indexed document."""

    def _retrieve(self, query_bundle: QueryBundle) -> List[NodeWithScore]:
        return []


def _readable_paths(chroma_collection, username: str | None, role: str) -> list[str]:
    """Distinct indexed file_path values the user may read (sorted)."""
    from src.documents import can_read, indexed_path_owner
    if not username:
        return []  # deny by default: no username means nothing is readable
    metadatas = chroma_collection.get(include=["metadatas"]).get("metadatas") or []
    paths = {m.get("file_path") for m in metadatas if m}
    return sorted(p for p in paths
                  if isinstance(p, str)
                  and can_read(username, role, indexed_path_owner(p)))


def build_rag_retriever(username: str | None, role: str):
    """Builds the hybrid RAG retriever for one user (#45).

    ADMIN gets the unfiltered retriever. Any other role only gets chunks whose
    indexed owner folder matches the username (src.documents.can_read). The
    owner filter is applied before ranking (Chroma 'in' filter on the allowed
    file_path values, BM25 weight mask) and again on every sub-retriever result.
    """
    db = chromadb.PersistentClient(path=str(CHROMA_PATH))
    chroma_collection = db.get_or_create_collection(COLLECTION_NAME)

    filtered = role != "ADMIN"
    allowed_paths: list[str] = []
    if filtered:
        allowed_paths = _readable_paths(chroma_collection, username, role)
        if not allowed_paths:
            return _EmptyRetriever()

    vector_store = ChromaVectorStore(chroma_collection=chroma_collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    index = VectorStoreIndex.from_vector_store(vector_store, storage_context=storage_context)

    if filtered:
        owner_filters = MetadataFilters(filters=[
            MetadataFilter(key="file_path", operator=FilterOperator.IN, value=allowed_paths)
        ])
        vector_retriever = OwnerFilteredRetriever(
            index.as_retriever(similarity_top_k=RETRIEVER_TOP_K, filters=owner_filters),
            username, role)
    else:
        vector_retriever = index.as_retriever(similarity_top_k=RETRIEVER_TOP_K)

    retriever = vector_retriever
    if BM25_PATH.exists():
        try:
            bm25_retriever = BM25Retriever.from_persist_dir(str(BM25_PATH))
            bm25_retriever.similarity_top_k = RETRIEVER_TOP_K
            if filtered:
                allowed = set(allowed_paths)
                bm25_retriever.corpus_weight_mask = [
                    1 if entry.get("file_path") in allowed else 0
                    for entry in bm25_retriever.corpus
                ]
                bm25_retriever = OwnerFilteredRetriever(bm25_retriever, username, role)
            retriever = QueryFusionRetriever(
                [vector_retriever, bm25_retriever],
                similarity_top_k=RETRIEVER_TOP_K,
                num_queries=1,
                mode="reciprocal_rerank",
                use_async=False, verbose=True
            )
        except Exception as e:
            slog.warning(f"BM25 load failed, using vector-only retriever: {e}")
    return retriever


def load_rag_engine(user_role="DEFAULT", username=None):
    """RAG Engine: Searches the Vector Database.

    username scopes retrieval to the user's own documents for every role other
    than ADMIN; a missing username for such a role means nothing is readable.
    """
    retriever = build_rag_retriever(username, user_role)

    selected_prompt = ROLE_PROMPTS.get(user_role, DEFAULT_SYSTEM_PROMPT)
    owner_filter = "off" if user_role == "ADMIN" else "on"
    slog.info(f"RAG Engine Loaded | Profile: {user_role} | owner filter: {owner_filter}")

    return ContextChatEngine.from_defaults(
        retriever=retriever,
        memory=ChatMemoryBuffer.from_defaults(token_limit=8192),
        system_prompt=selected_prompt,
        context_template=CUSTOM_CONTEXT_TEMPLATE,
        llm=Settings.llm
    )


def load_simple_local_engine():
    """Local Engine: Llama. Sees uploaded files."""
    return SimpleChatEngine.from_defaults(
        system_prompt="Sei un analista di dati. Rispondi basandoti ESCLUSIVAMENTE sul testo fornito, se presente.",
        llm=Settings.llm,
        memory=ChatMemoryBuffer.from_defaults(token_limit=16384)
    )


def load_cloud_engine():
    """Cloud Engine: Gemini. Sees chat + Global Knowledge."""
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key: raise ValueError("Missing GOOGLE_API_KEY in .env file")

    llm_cloud = Gemini(model="models/gemini-2.5-flash", api_key=api_key)

    return SimpleChatEngine.from_defaults(
        system_prompt=(
            "Sei Gemini, un'IA avanzata di Google. "
            "Usa la tua vasta conoscenza per rispondere a domande su aziende, concetti o dati esterni. "
            "Non limitarti a riassumere la chat."
        ),
        llm=llm_cloud,
        memory=ChatMemoryBuffer.from_defaults(token_limit=8192)
    )
