# Study export is the durable record, not a server archive

**Status:** accepted

The server keeps studies in memory for 24 hours and adds a signed, short-lived download link for a self-contained JSON export. The export carries the exact orbit records, accepted setup, evidence, software versions, assumptions, results, and a replay block. We chose this over a durable server-side archive to avoid storage, retention, privacy, and migration work in this increment.

**Consequences:** links stop working after the result expires or the server restarts; researchers must save the file. Replaying is manual until an import tool is added. The export schema is versioned so a later importer can read earlier files.
