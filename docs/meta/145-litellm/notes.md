# Notes

## 2026-09-13 — repository implementation

The upstream contract was validated against LiteLLM's proxy documentation: `database_url`
is a supported external PostgreSQL setting; `/health/readiness` includes database readiness;
and provider routes can use `os.environ/<name>` references. The implementation keeps route
configuration out of the database (`store_model_in_db: false`) and keeps provider keys out
of both the ConfigMap and the PBS artifact. The database dump is custom-format so restore
can clean and reload the named target database, while the archived ConfigMap is applied by
the controller after a successful restore job.

No provider account, Kubernetes target, PostgreSQL instance, or disposable namespace was
named for live work. That evidence is intentionally deferred to #198 as required by #145.
