const CACHE_NAME = 'fuyi-ac-v3';
const ASSETS_TO_CACHE = [
    '/',
    '/ad',   // 廣告頁也要快取
    '/home', // 查詢大廳
    '/static/manifest.json',
    '/static/icon-192.png',
    '/static/icon-512.png',
    // 外部資源 (Tailwind, Fonts, Icons)
    'https://cdn.tailwindcss.com?plugins=forms,typography',
    'https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600;700;800&display=swap',
    'https://fonts.googleapis.com/icon?family=Material+Icons+Outlined|Material+Icons+Round'
];

// 1. 安裝階段：快取核心靜態檔案
self.addEventListener('install', (event) => {
    // 強制立即接管控制權，不用等下次重新整理
    self.skipWaiting();
    
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            console.log('✅ Service Worker: 已安裝並快取核心檔案');
            return cache.addAll(ASSETS_TO_CACHE);
        })
    );
});

// 2. 啟動階段：清理舊版本的快取
self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keyList) => {
            return Promise.all(keyList.map((key) => {
                if (key !== CACHE_NAME) {
                    console.log('🧹 Service Worker: 清除舊快取', key);
                    return caches.delete(key);
                }
            }));
        })
    );
    // 讓 Service Worker 立即控制所有頁面
    return self.clients.claim();
});

// 3. 抓取階段：採用「網路優先 (Network First)」策略
self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);
    
    // 忽略非 GET 請求
    if (event.request.method !== 'GET') return;

    // 特別處理詳情頁：如果是離線且沒快取，嘗試導向到一個可以從 IndexedDB 讀取資料的殼
    if (url.pathname.startsWith('/detail/')) {
        event.respondWith(
            fetch(event.request)
                .then(response => {
                    const copy = response.clone();
                    caches.open(CACHE_NAME).then(cache => cache.put(event.request, copy));
                    return response;
                })
                .catch(async () => {
                    const cacheRes = await caches.match(event.request);
                    if (cacheRes) return cacheRes;
                    
                    // 如果連快取都沒有，回傳 /home 讓使用者能從最近紀錄點擊 (那裡有資料)
                    // 或者回傳一個通用殼 (這裡我們先回傳快取的 /home 作為備案)
                    return caches.match('/home');
                })
        );
        return;
    }

    event.respondWith(
        fetch(event.request)
            .then((response) => {
                // 如果網路請求成功
                // 1. 複製一份回應 (因為 response stream 只能用一次)
                const responseClone = response.clone();
                
                // 2. 把最新的資料存入快取 (更新備份)
                caches.open(CACHE_NAME).then((cache) => {
                    cache.put(event.request, responseClone);
                });

                // 3. 回傳最新的資料給使用者
                return response;
            })
            .catch(() => {
                // 如果網路請求失敗 (斷網/離線)
                // 從快取中尋找備份
                console.log('⚠️ Service Worker: 網路離線，切換至快取模式');
                return caches.match(event.request);
            })
    );
});