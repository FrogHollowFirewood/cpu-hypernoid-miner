# Security and operational reports

The maintained public release is CPU HyperNOID Miner v1.0.0. Report ordinary setup or compatibility problems through the repository's Issues page. If GitHub offers a private vulnerability-reporting form for this repository, use that form for an exploitable issue. Otherwise, open a minimal issue requesting a private reporting route without publishing exploit details or secrets.

Never send a seed phrase, private key, access token, remote desktop password, or complete environment dump. A public receiving address is enough to configure this miner; the program does not need custody of a wallet.

Download releases from this project's GitHub account and verify the release checksums. The runtime manifest detects mismatched package files. It does not authenticate a malicious replacement of both code and manifest. Version 1.0.0 does not claim signed release artifacts or an independent third-party security certification.

Run the public launcher as an ordinary user. Directly starting an engine bypasses launcher checks such as temperature monitoring and the instance lock. The temperature guard polls supported Linux sensors; it is not a replacement for hardware protection, working cooling, or a CPU-specific operating limit.

The TLS client verifies certificates and hostnames. Custom trust is supported through an explicit CA file. There is no insecure TLS switch. Functional pidfd support is required so shutdown can target the miner's own processes without relying only on reusable process IDs.
