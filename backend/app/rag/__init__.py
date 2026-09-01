"""Retrieval-augmented generation: embeddings, ingestion, and search.

``embeddings`` defines the vendor-neutral contract and its implementations,
``chunking`` splits documents, and the services in ``app.services`` compose
them. Callers depend on the protocol, never on a concrete embedder.
"""
