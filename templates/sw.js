// Служебный файл приложения (service worker).
// Нужен ТОЛЬКО для того, чтобы приложение можно было «установить» на телефон
// или компьютер (иконка на рабочем столе). Ничего не кэширует — страницы всегда
// самые свежие. На работу самого мессенджера не влияет.

self.addEventListener("install", function () {
  self.skipWaiting();
});

self.addEventListener("activate", function (event) {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", function () {
  // Ничего не перехватываем — пусть браузер грузит как обычно.
});
