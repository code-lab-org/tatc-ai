# Study export is the durable record, not a server archive

**Status:** accepted

The server keeps studies in memory for 24 hours and adds a signed download link for a self-contained JSON export. The link is valid for 15 minutes. The export carries the exact orbit records, accepted setup, evidence, software versions, assumptions, results, and a replay block. We chose this over a durable server-side archive to avoid storage, retention, privacy, and migration work in this increment.

**Trust model:** the download route is outside the OIDC-protected MCP endpoint. The signed token is the authorization. Anyone who has the link can download that one file; the route cannot list results or read another study.

**Consequences:** links stop working after 15 minutes, after the result expires, or after the server restarts; researchers must save the file. Replaying repeats the setup choices, not the recorded orbit data; a new run fetches current elements and must be checked against the recorded orbit hash. An import tool remains future work. The export schema is versioned so a later importer can read earlier files.
