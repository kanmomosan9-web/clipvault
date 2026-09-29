// ホーム画面に追加（アプリとしてインストール）できるようにするための最小構成。
// 画面や動画はキャッシュしない。常にサーバーの最新を表示する。
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
