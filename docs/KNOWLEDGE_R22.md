# Local knowledge and memory — r22

## Data layout

- `%LOCALAPPDATA%\LocalAssistant\knowledge.sqlite3` stores imported document text chunks, source locators, metadata, optional vector blobs and explicit memory entries.
- Original files are not moved or modified. Removing an indexed document only removes its extracted/indexed representation.
- `conversations.sqlite3` and existing conversation rows are not changed by this feature.

## Import and extraction

Supported formats: PDF with a text layer, DOCX, TXT and Markdown. Per-file size is limited to 40 MiB; extracted text to 20 MiB; PDF pages to 5,000; DOCX ZIP entries to 1,200 and total uncompressed size to 160 MiB. DOCX compression ratios are checked. Scanned PDFs and OCR are not supported in this release.

Text is split into overlapping chunks with page/paragraph/table locators. SHA-256 makes re-import of an unchanged path idempotent. Re-import of a changed file replaces its previous index in one SQLite transaction.

## Retrieval

FTS5 is preferred. If the SQLite build lacks FTS5, a parameterized LIKE search is used. Memory title, content, category and tags are searchable. Semantic search is optional and applies to document chunks; FTS remains available when embeddings are not configured or an embedding request fails. Hybrid results use reciprocal-rank fusion. Before creating a query embedding, the app checks that at least one document is indexed with the selected embedding model. The in-process semantic scan is capped at the latest 20,000 indexed chunks and at 64 MiB of raw vector payload (whichever limit is reached first). The scan reads only chunk IDs and vectors; chunk text and source metadata are loaded only for the top-k results, keeping transient memory bounded independently of the total candidate text size.

## Embeddings

The application uses the same `OllamaClient` and request lock for embeddings as for chat, classifier and warm-up. This prevents concurrent requests from using the shared `requests.Session` and model server at the same time. Embedding requests have per-text and batch limits. If a user cancels while an embedding request owns the shared lock, only that embedding response is closed; cancelling while waiting for the lock does not interrupt an unrelated chat request.

Document indexing writes vectors to `embedding_staging` in bounded batches, validates consistent dimensions and publishes all vectors in a single transaction. Cancellation is checked before and after each embedding batch and immediately before the publish transaction; a cancelled or failed job cleans staging rows and keeps the prior active vectors. Import extraction and chunk insertion also check cancellation between bounded units; the database transaction rolls back if cancellation is observed before commit. If cancellation arrives after the text/FTS import commits but before embeddings finish, the document remains available through full-text search and the UI reports the semantic-index step as cancelled. Staging rows left by a process crash are cleaned after 48 hours.

## Privacy

Full-text indexing itself is local. With semantic search enabled, document fragments are sent to the configured Ollama endpoint for vector generation; search queries are also sent there. The selected answer model receives retrieved snippets as context. If Ollama is configured to a remote endpoint, that server receives prompts and document-derived context. The settings dialog warns about this.

Retrieved content is explicitly marked as untrusted reference data and not as instructions. A second policy is added to the system message whenever retrieved material is attached; it tells the model to ignore instructions found inside sources, distinguish facts from assumptions and cite the source locator. The content is appended only to the transient request context; it is not inserted into the stored user message. The memory feature is explicit and user-managed: there is no automatic extraction of personal facts from conversations. The number of memory entries injected into a response is configurable. A knowledge database error is isolated from the normal chat startup path; retrieval is skipped while the knowledge store is unavailable.

The application does not encrypt SQLite databases at rest. Imported text chunks, saved memories and conversation history are stored under `%LOCALAPPDATA%\LocalAssistant`; rely on Windows account permissions and full-disk encryption, and do not import sensitive material on an unprotected device. If the configured Ollama endpoint is remote, embedding requests, retrieval queries, prompts and retrieved snippets are sent to that server.

## Schema

`PRAGMA user_version` tracks knowledge DB schema version 2. Migration rebuilds the memory FTS index with its new `tags` column while preserving memory rows. A database with a newer schema version is rejected before DDL runs, so an older application does not silently downgrade or modify it.
