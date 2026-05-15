# Changelog

<<<<<<< release-please--branches--main
## [0.11.3](https://github.com/haidj01/finly-agent/compare/v0.11.2...v0.11.3) (2026-05-15)


### Bug Fixes

* security hardening for strategy/regime pipeline ([#31](https://github.com/haidj01/finly-agent/issues/31)) ([f9abd49](https://github.com/haidj01/finly-agent/commit/f9abd496e7fc3d35e798d98c7d7f86afa3f13f9d))

## [0.11.2](https://github.com/haidj01/finly-agent/compare/v0.11.1...v0.11.2) (2026-05-15)


### Bug Fixes

* add start date to SPY bars request; drop feed=iex ([3a532fc](https://github.com/haidj01/finly-agent/commit/3a532fc1dc9621f90d6192ca5b6fb4e49bb5a2ea))

## [0.11.1](https://github.com/haidj01/finly-agent/compare/v0.11.0...v0.11.1) (2026-05-14)


### Bug Fixes

* move logger after imports to satisfy ruff E402 ([38329ba](https://github.com/haidj01/finly-agent/commit/38329ba3ccc98b38315d03349d7d6d75924ae3b6))
* use IEX feed for bars API and add regime error logging ([#28](https://github.com/haidj01/finly-agent/issues/28)) ([2dae21d](https://github.com/haidj01/finly-agent/commit/2dae21db7b54acbe0f99d29971e79c490f5f58dc))

## [0.11.0](https://github.com/haidj01/finly-agent/compare/v0.10.0...v0.11.0) (2026-05-14)


### Features

* add mode-aware Alpaca account/trading routes to agent ([2c16946](https://github.com/haidj01/finly-agent/commit/2c1694698fc7e853ad7e49668c147cf358fd96c3))
* market regime filtering + AI recommendations + global trading mode ([cee40e8](https://github.com/haidj01/finly-agent/commit/cee40e88813d0d29dfa0d46dceff684632a13211))

## [0.10.0](https://github.com/haidj01/finly-agent/compare/v0.9.0...v0.10.0) (2026-05-14)


### Features

* add Claude-powered regime strategy recommendations endpoint ([679db73](https://github.com/haidj01/finly-agent/commit/679db7358cf2f6e6e35788c96cb57ba6f58ef702))
* proxy regime-recommendations from finly-agent ([9b32e31](https://github.com/haidj01/finly-agent/commit/9b32e317d3b795fe01fea126650b41765cefdb8c))

## [0.9.0](https://github.com/haidj01/finly-agent/compare/v0.8.0...v0.9.0) (2026-05-14)


### Features

* A+B market regime strategy filtering ([1808d59](https://github.com/haidj01/finly-agent/commit/1808d59bf4d194a9976c868fc0f6f44a34f42726))
* implement A+B market regime strategy filtering ([ca5a766](https://github.com/haidj01/finly-agent/commit/ca5a7664d385ad223c8d0c9b2a71ae540d7687da))

## [0.8.0](https://github.com/haidj01/finly-agent/compare/v0.7.0...v0.8.0) (2026-05-14)


### Features

* add source filter (strategy/watchdog) to trade-history endpoint ([87e769a](https://github.com/haidj01/finly-agent/commit/87e769a6f1c6ab06c2ae869cd938e06ca0afb1a6))
* add source filter to trade-history endpoint ([641da11](https://github.com/haidj01/finly-agent/commit/641da1113f7016f169031cf5387cc75d41296a13))

## [0.7.0](https://github.com/haidj01/finly-agent/compare/v0.6.0...v0.7.0) (2026-05-14)


### Features

* separate strategy management per account mode (paper / live) ([a8b2e3b](https://github.com/haidj01/finly-agent/commit/a8b2e3b8778d078fa948074ca0bd911a00583baa))

## [0.6.0](https://github.com/haidj01/finly-agent/compare/v0.5.0...v0.6.0) (2026-05-13)


### Features

* add GET/PUT /market/trading-mode endpoints ([a7ae77c](https://github.com/haidj01/finly-agent/commit/a7ae77cb49f78c5f1c2dda0f523bd8103d8e31d6))
* trade history, strategy engine, market regime, and live/paper trading mode ([4b7d2f1](https://github.com/haidj01/finly-agent/commit/4b7d2f145ff97bb931eb3ef0772958c684ede094))


### Bug Fixes

* persist watchdog config to /data alongside the database ([40ef70a](https://github.com/haidj01/finly-agent/commit/40ef70ada570513faacc6aab00b423611bdb7f68))

## [0.5.0](https://github.com/haidj01/finly-agent/compare/v0.4.0...v0.5.0) (2026-05-13)


### Features

* Alpaca live account support + live key CD injection ([8acb46c](https://github.com/haidj01/finly-agent/commit/8acb46c8c41d12dc2bdee20275087eebbfbeb1fb))
* inject Alpaca live keys via Secrets Manager on every deploy ([996a673](https://github.com/haidj01/finly-agent/commit/996a6730a8ad9b615e279422c50c7ea7338ccbb6))

## [0.4.0](https://github.com/haidj01/finly-agent/compare/v0.3.0...v0.4.0) (2026-05-13)


### Features

* add strategy engine P1–P5 + market regime classification ([118dabd](https://github.com/haidj01/finly-agent/commit/118dabdf18ab38eea0a0a5b25fcd86431968e7b9))
* add trade history endpoint with strategy metadata ([7670a1a](https://github.com/haidj01/finly-agent/commit/7670a1aa438cb3459148a0a81a7e2b29dcc39d5a))
* add trade history endpoint with strategy metadata ([eb0e9a4](https://github.com/haidj01/finly-agent/commit/eb0e9a4374888a332d06181edc2c4872b4e11b71))
* expose /version endpoint for deployment version display ([b29274c](https://github.com/haidj01/finly-agent/commit/b29274c96a32b687c25b41585a9b72491321a2fc))
* expose /version endpoint for deployment version display ([f0bdade](https://github.com/haidj01/finly-agent/commit/f0bdade4cad9e91e3ced94fc1191be4b3124d4b7))
* strategy engine P1–P5 + market regime classification ([c1d42f9](https://github.com/haidj01/finly-agent/commit/c1d42f9428289cdd3a50ef66572adb02b03ceb58))


### Bug Fixes

* read DB_PATH from environment variable for persistent storage ([3b4035c](https://github.com/haidj01/finly-agent/commit/3b4035cee65dd923a225f8370f9f33dea57e54d7))
* resolve merge conflict in .release-please-manifest.json ([655634e](https://github.com/haidj01/finly-agent/commit/655634eabd9e205a8f6940041df40359b1524e69))
* resolve ruff CI failures ([c7fea1c](https://github.com/haidj01/finly-agent/commit/c7fea1c14ae99825467c57852c7098056b257fe1))
* resolve ruff CI failures on main ([d7bee3b](https://github.com/haidj01/finly-agent/commit/d7bee3b998bd3ec7c39f25579f774560064ac1ec))

## [0.3.0](https://github.com/haidj01/finly-agent/compare/v0.2.0...v0.3.0) (2026-05-06)


### Features

* add trade history endpoint with strategy metadata ([7670a1a](https://github.com/haidj01/finly-agent/commit/7670a1aa438cb3459148a0a81a7e2b29dcc39d5a))
* add trade history endpoint with strategy metadata ([eb0e9a4](https://github.com/haidj01/finly-agent/commit/eb0e9a4374888a332d06181edc2c4872b4e11b71))
=======
## [0.2.1](https://github.com/haidj01/finly-agent/compare/v0.2.0...v0.2.1) (2026-04-23)
>>>>>>> main


### Bug Fixes

* read DB_PATH from environment variable for persistent storage ([3b4035c](https://github.com/haidj01/finly-agent/commit/3b4035cee65dd923a225f8370f9f33dea57e54d7))

## [0.2.0](https://github.com/haidj01/finly-agent/compare/v0.1.0...v0.2.0) (2026-04-23)


### Features

* expose /version endpoint for deployment version display ([b29274c](https://github.com/haidj01/finly-agent/commit/b29274c96a32b687c25b41585a9b72491321a2fc))
* expose /version endpoint for deployment version display ([f0bdade](https://github.com/haidj01/finly-agent/commit/f0bdade4cad9e91e3ced94fc1191be4b3124d4b7))


### Bug Fixes

* resolve ruff CI failures ([c7fea1c](https://github.com/haidj01/finly-agent/commit/c7fea1c14ae99825467c57852c7098056b257fe1))
* resolve ruff CI failures on main ([d7bee3b](https://github.com/haidj01/finly-agent/commit/d7bee3b998bd3ec7c39f25579f774560064ac1ec))
