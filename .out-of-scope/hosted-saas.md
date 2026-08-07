# Hosted SaaS

**Decision.** There is no hosted version, no tenants, no accounts, no
waitlist. This will not become a service that holds anyone's books.

**Reasoning.** The entire safety story is that your data lives on your
disk and your own private remote, readable by tools you can inspect.
The moment someone else hosts it, that story inverts: custodianship,
credentials at scale, a breach target, compliance surface, and a
business model with incentives pointed at your attention instead of
your money. Every good property of this system falls out of NOT being
that.

**Escape hatch.** Fork it — you are your own tenant. Everything ships
as markdown and small Python; `install.sh` does the whole setup, and
the optional phone Worker runs on YOUR Cloudflare account against YOUR
private repo. Operating it yourself is not the consolation prize; it is
the product.
