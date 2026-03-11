"""RAG pipeline: download PDFs, chunk, embed, store in FAISS, and expose retriever tool."""

import os
import logging
from pathlib import Path
from typing import Annotated

import threading

import boto3
import requests
from langchain_community.document_loaders import PyPDFLoader
from langchain_community.vectorstores import FAISS
from langchain_aws import BedrockEmbeddings
from langchain_core.tools import tool
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import settings

logger = logging.getLogger(__name__)

DOCUMENTS = {
    "amazon_2024_annual_report.pdf": "https://s2.q4cdn.com/299287126/files/doc_financials/2025/ar/Amazon-2024-Annual-Report.pdf",
    "amzn_q3_2025_earnings.pdf": "https://s2.q4cdn.com/299287126/files/doc_financials/2025/q3/AMZN-Q3-2025-Earnings-Release.pdf",
    "amzn_q2_2025_earnings.pdf": "https://s2.q4cdn.com/299287126/files/doc_financials/2025/q2/AMZN-Q2-2025-Earnings-Release.pdf",
}

_vectorstore: FAISS | None = None
_vectorstore_lock = threading.Lock()


def _get_embeddings() -> BedrockEmbeddings:
    return BedrockEmbeddings(
        model_id="amazon.titan-embed-text-v2:0",
        region_name=settings.aws_region,
    )


def download_documents(data_dir: str = settings.rag_data_dir) -> list[Path]:
    """Download PDFs if not already present. Returns list of local paths."""
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    paths = []
    for filename, url in DOCUMENTS.items():
        dest = Path(data_dir) / filename
        if not dest.exists():
            logger.info(f"Downloading {filename}...")
            resp = requests.get(url, timeout=120)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            logger.info(f"Saved {dest}")
        else:
            logger.info(f"Already exists: {dest}")
        paths.append(dest)
    return paths


def build_vectorstore(
    data_dir: str = settings.rag_data_dir,
    index_path: str = settings.faiss_index_path,
) -> FAISS:
    """Build FAISS index from PDFs and persist to disk."""
    paths = download_documents(data_dir)
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )

    all_docs = []
    for pdf_path in paths:
        loader = PyPDFLoader(str(pdf_path))
        docs = loader.load()
        chunks = splitter.split_documents(docs)
        logger.info(f"{pdf_path.name}: {len(docs)} pages → {len(chunks)} chunks")
        all_docs.extend(chunks)

    embeddings = _get_embeddings()
    vs = FAISS.from_documents(all_docs, embeddings)
    vs.save_local(index_path)
    logger.info(f"FAISS index saved to {index_path} ({len(all_docs)} total chunks)")
    return vs


def _download_index_from_s3(index_path: str) -> bool:
    """Download index.faiss and index.pkl from S3. Returns True if successful."""
    bucket = settings.s3_bucket_name
    if not bucket:
        return False
    try:
        s3 = boto3.client("s3", region_name=settings.aws_region)
        dest = Path(index_path)
        dest.mkdir(parents=True, exist_ok=True)
        for filename in ("index.faiss", "index.pkl"):
            s3_key = f"faiss_index/{filename}"
            local_file = dest / filename
            logger.info(f"Downloading s3://{bucket}/{s3_key} → {local_file}")
            s3.download_file(bucket, s3_key, str(local_file))
        return True
    except Exception as e:
        logger.warning(f"Could not download FAISS index from S3: {e}")
        return False


def load_vectorstore(index_path: str = settings.faiss_index_path) -> FAISS:
    """Load FAISS index from disk, fetching from S3 first if not present locally.

    Thread-safe: if the background startup thread and a request handler call
    this concurrently, only one will build/download; the other will block and
    then return the already-loaded singleton.
    """
    global _vectorstore
    if _vectorstore is not None:
        return _vectorstore

    with _vectorstore_lock:
        if _vectorstore is not None:  # re-check after acquiring lock
            return _vectorstore

        embeddings = _get_embeddings()
        if not Path(index_path).exists():
            if not _download_index_from_s3(index_path):
                logger.info("No FAISS index in S3 — building from scratch...")
                _vectorstore = build_vectorstore(index_path=index_path)
                return _vectorstore

        logger.info(f"Loading existing FAISS index from {index_path}")
        _vectorstore = FAISS.load_local(
            index_path, embeddings, allow_dangerous_deserialization=True
        )
        return _vectorstore


@tool
def retrieve_from_knowledge_base(
    query: Annotated[str, "Question or topic to search in Amazon financial reports"],
    k: Annotated[int, "Number of document chunks to retrieve"] = 5,
) -> str:
    """Search Amazon financial reports (2024 Annual Report, Q2/Q3 2025 Earnings) for relevant information."""
    vs = load_vectorstore()
    docs = vs.similarity_search(query, k=k)
    if not docs:
        return "No relevant documents found."

    results = []
    for i, doc in enumerate(docs, 1):
        source = doc.metadata.get("source", "unknown")
        page = doc.metadata.get("page", "?")
        results.append(f"[{i}] Source: {Path(source).name}, Page {page}\n{doc.page_content.strip()}")

    return "\n\n---\n\n".join(results)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    build_vectorstore()
