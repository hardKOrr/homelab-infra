# Forgejo

## Hosting

Forgejo defaults to Docker on its services VM. Set `hosting.method: kubernetes` in the
instance config to use the cluster, retaining its registered PostgreSQL backend. Its
`app.storage` GiB claim uses the cluster's default StorageClass. HTTP uses the instance's
usual hostname through the ingress; Git SSH advertises the first cluster node's address
and `app.ssh_node_port` (default 30222). A second instance needs an unused SSH NodePort;
Deploy checks the cluster-wide claim before applying it. An enabled SSH forward targets
that node and must set `local_port` to `app.ssh_node_port`. SSH is separate from the HTTP route.

Both Forgejo methods keep the same `data.pxar` layout: `forgejo/` contains the complete
application data and `database/forgejo.dump` contains its registered PostgreSQL database.
Kubernetes quiesces Forgejo through the shared backup Job, dumps the database through the
shared client, and copies that dump into the archive layout before upload. Restore reads
that nested dump through the shared recovery Job, allowing recovery points to move between
Docker and Kubernetes without regenerating Forgejo's application secret key.

## Native recovery

Forgejo native recovery stops only the selected service and stores a PostgreSQL dump and
the complete data directory (repositories, LFS, attachments, and `app.ini` secrets) in
one PBS `data.pxar` under `host/<instance>`. The service stays stopped while the database
and files are captured. Restore stages and validates both parts, keeps the existing data
directory beside its original path and a database dump until Forgejo passes its health
check, and restores both originals on failure. It leaves Forgejo stopped after a failed
restore and retains the two newest recovery points alongside daily retention.
