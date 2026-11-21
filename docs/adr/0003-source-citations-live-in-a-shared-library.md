# Source citations live in a shared library

**Status:** accepted

Instrument configurations repeated a document's title, URL, revision, and retrieval time once per excerpt. The AMSR2 record has 11 evidence entries from 5 documents, and 7 of them quote the same TAT-C validation notebook. We added a source library: one immutable JSON file per document revision, holding the document metadata and its excerpts, each with a locator and the copied text.

A configuration cites an excerpt by document ID, revision, and excerpt ID. It keeps the interpretation and the supported fields as a claim, because the same passage can support different parameters in different configurations. Resolution happens at load, so the rest of the server keeps consuming the existing evidence shape and no tool output changes.

**Consequences:** a source change creates a new library revision and never edits an earlier one. The claim form uses the next configuration schema version and is normalized to the current evidence shape on load, so tool responses and snapshots keep their existing fields. A study copies the resolved receipt into its accepted setup and export, so a later library edit cannot change an earlier study. Documents are never downloaded, parsed, or hosted. Review and sign-off records are deferred; the library records what a source says, not who checked it.
