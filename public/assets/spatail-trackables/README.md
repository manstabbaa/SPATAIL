# SPATAIL trackables

Reference-object library for the TRACKED stream. Drop `<id>.referenceobject`files (trained on the Mac via Create ML / `xcrun createml objecttracker`) here;
optional `<id>.json` sidecar: {name, tracking: detection|tracking, subjects: []}.
Served by job_server at GET /trackables (index) + /trackables/<file> (bytes).
The phone downloads these at runtime - no app rebuild (ARReferenceObject(archiveURL:)).
