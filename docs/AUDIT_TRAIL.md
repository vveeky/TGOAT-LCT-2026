# Durable audit trail

Normal PostgreSQL operational mode contains an append-only audit journal. Critical workflow mutations and their audit record share the request Unit of Work so an injected audit failure rolls the domain mutation back.

Audit records include actor, cumulative roles/scopes, correlation/request context, action, resource, outcome, timestamps and safe before/after metadata where supplied. Passwords, bearer tokens, private credentials and raw sensitive request bodies are excluded.

Coverage includes authentication events, authorization denials, privileged reads, Signal decisions, Request lifecycle, technician/result lifecycle and access-administration changes. The audit API is read-only and itself capability/scope protected.

Database protections reject mutation/deletion/truncation of the audit journal. Historical Replay is a resettable isolated demo session and is not claimed to provide permanent production audit retention across replay resets.
