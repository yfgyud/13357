// 大肥鱼 · 桌面 AI  ——  Service Worker
// 作用：① 缓存应用外壳，断网也能打开；② 让 Chrome 在安卓上弹出「添加到主屏」安装提示。
const CACHE = 'fatfish-v2';
const SHELL = [
  '/',
  '/fatfish.html',
  '/manifest.webmanifest',
  '/icon.png',
  '/pet.png',
  '/sw.js',
];

// 安装时把外壳资源装进缓存
self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE)
      .then((c) => c.addAll(SHELL))
      .then(() => self.skipWaiting())
  );
});

// 激活时清理旧缓存，并立即接管页面
self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;            // 非 GET 直接走网络（含 /api 的 POST）
  const url = new URL(req.url);

  // API 请求（POST /api/*）：交给网络，离线时由前端规则兜底，这里不拦截
  if (url.pathname.startsWith('/api/')) return;

  // 静态资源 / 导航：缓存优先，拿不到再走网络并顺手补缓存
  e.respondWith(
    caches.match(req).then((cached) => {
      if (cached) return cached;
      return fetch(req).then((resp) => {
        if (resp.ok && url.origin === self.location.origin) {
          const copy = resp.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
        }
        return resp;
      }).catch(() => caches.match('/fatfish.html'));
    })
  );
});
