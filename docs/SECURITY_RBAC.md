# Security and RBAC

Normal operational authorization is capability + resource-scope based and supports cumulative roles/groups.

Customer-confirmed role semantics used by the project:

- ODS/central dispatcher: broad operational scope;
- operational/district dispatcher: assigned district/scope;
- technician: assigned complex/work scope;
- IS administrator: identity/access administration, not implicit dispatcher authority;
- roles may be combined.

Manager/analyst are product personas/capability profiles; they are not silently treated as confirmed customer LDAP groups.

Server-side checks protect direct-ID access, pagination/counts, commands and administrative APIs. Synthetic map placement is not an authorization source.

LDAP mode uses LDAPS or STARTTLS and exact group mappings. Unknown directory groups grant nothing. Sessions are short-lived and directory unavailability fails closed. `TGOAT_LDAP_INSECURE_LOCAL_TEST=1` is limited to explicit local testing.

TLS termination is configured in nginx with TLS 1.2/1.3. Secrets/private keys are supplied outside the repository.
