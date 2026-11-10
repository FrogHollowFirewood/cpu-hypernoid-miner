---
layout: default
title: The 3% miner-use fee
---

# The 3% miner-use fee

CPU HyperNOID Miner v1.0.0 includes a **3% miner-use fee measured by scheduled runtime allocation**. During a fee window, the miner searches for shares that pay the maintainer's published receiving address. During the user window, it uses the pool and receiving address supplied by the user.

The fee is part of the public release. It is disclosed in the startup output, the check output, this guide, the source, and `launcher/fee-policy.json`. There is no claim that this miner is fee-free.

## Exact schedule

Each process run starts with the user phase:

| Part of each cycle | Duration | Destination |
|---|---|---|
| User phase | 5,820 seconds: 97 minutes | User-supplied pool and wallet |
| Fee phase | Up to 180 seconds: 3 minutes | Published developer pool and wallet below |
| Full cycle | 6,000 seconds: 100 minutes | Then repeat |

The scheduled fraction is `180 / 6000 = 0.03`, or 3%. The schedule uses a monotonic clock, so a normal wall-clock correction does not create extra fee time. Restarting the miner starts a new cycle with user work. The program does not preserve a fee debt across restarts.

This is a **time allocation**, not a deduction from the user's wallet and not a guarantee that exactly 3% of accepted shares, coins, revenue, or pool payouts will go to the maintainer. The miner does not have a private key and cannot spend from a wallet.

Connection setup, authorization, pool downtime, and work interruptions occur inside the scheduled windows. Shares are probabilistic, pools can use different difficulties and fees, and a short session may end before its first fee window. The fee phase reserves up to four seconds of its slot for graceful shutdown, then uses forced cleanup if necessary. A slow user-to-fee handover can shorten the available fee slot. Unused time returns to user work. Operating-system scheduling and in-flight hash batches prevent a mathematical hard cap on individual executed hashes. These factors make credited work and payout percentages differ from the nominal time allocation.

## Published fee destination

| Setting | Value |
|---|---|
| Fee percentage | 3% of scheduled runtime |
| Connection | Certificate-verified TLS |
| Pool hostname | `us3.innovlab.cc` |
| Pool port | `19601` |
| Worker label | `HyperNOID_DevFee` |
| Receiving address | `o1206vtktn5pqhtnc9h689rgur2lc04jv20k6e7093xfpmc4jdr8rqfw9fs0` |

The maintainer authorized this public destination. The hostname, port, and receiving address were checked against the existing mining configuration when this release was prepared. Their publication does not imply that the pool guarantees future availability or endorses this project.

The developer endpoint uses the operating system's normal trusted certificate authorities. A user's `--ca-file` applies to that user's pool connection; it does not replace certificate trust for the fixed fee pool. Certificate and hostname checks remain enabled in both phases.

## What happens at a switch

The launcher stops the current client and verifies that its owned worker processes have ended. It then starts a fresh client and worker for the next phase. The user and fee workers do not run simultaneously. A new client starts with its own pool session, jobs, nonce namespace, pending-share queue, and counters.

Log messages identify the active user or fee phase. Accepted-share counts belong to the corresponding client session; do not add a fee session's shares to the user's payout total. Check the user's own pool dashboard when confirming credited work.

CPU selection, the selected thread count, the user lock, and temperature monitoring continue to apply. A temperature cutoff, sensor failure, package-integrity failure, or cleanup failure stops the miner rather than starting another phase.

## If the fee pool is unavailable

A fee connection, authentication, protocol, or certificate failure ends that fee attempt. The launcher skips the rest of that fee window and resumes user work. It does not redirect the fee to an undisclosed address, extend a later fee window, or build up a catch-up balance.

A slow fee connection remains bounded by the phase deadline. User work resumes after the old client and worker have been cleaned up. If that cleanup cannot be confirmed, the launcher stops.

`--once` affects ordinary user-session connection failures. A planned fee switch or a failed fee attempt does not, by itself, end a user's `--once` mining run.

## Pool fees are separate

The pool can charge its own fee or apply payout thresholds, maturity periods, minimum balances, or other rules. Those are controlled by the pool operator. The miner-use fee does not replace them. Read the chosen pool's terms and compare accepted shares with its dashboard.

## Inspect or verify the policy

From the extracted release folder:

```bash
cat launcher/fee-policy.json
./hypernoid-miner --help
./hypernoid-miner --check
sha256sum --check PACKAGE-SHA256SUMS
```

The policy file is part of the runtime manifest and the complete archive checksum inventory. Editing it inside an official extracted package causes a consistency failure unless the package is deliberately rebuilt. These checks detect changed files; they do not authenticate an untrusted replacement of both the program and its checksums.

The source remains available under Apache 2.0. A modified build should clearly identify itself and describe its payout behavior. The published v1.0.0 fee rate and destination are the values above.
