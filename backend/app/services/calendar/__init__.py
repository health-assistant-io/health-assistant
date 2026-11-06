"""Calendar connector service (ADR-0052 / plan 52 P2).

Host-owned domain mapping (:mod:`.mapping`), sealed credential custody
(:mod:`.credentials`) and the reconcile/apply sync pipeline
(:mod:`.sync`). The shared calendar engine (transports, reconcile) is the
vendored family contract in ``app.extensions.cloud.cloud_calendar`` — this
package never edits it.
"""
