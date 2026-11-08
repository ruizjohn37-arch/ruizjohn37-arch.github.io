// Shows short videos listed in videos/videos.json on the page.
// The section stays hidden until at least one video is listed for that page.
(function () {
  var box = document.querySelector("[data-videos]");
  if (!box) return;
  var page = box.getAttribute("data-videos");
  var base = box.getAttribute("data-base") || "videos/";
  fetch(base + "videos.json", { cache: "no-store" })
    .then(function (r) { return r.ok ? r.json() : {}; })
    .then(function (all) {
      var list = (all && all[page]) || [];
      if (!list.length) return;
      var grid = box.querySelector(".video-grid");
      list.forEach(function (v) {
        var fig = document.createElement("figure");
        var vid = document.createElement("video");
        vid.src = base + v.file;
        vid.controls = true;
        vid.playsInline = true;
        vid.muted = true;
        vid.loop = true;
        vid.preload = "metadata";
        if (v.poster) vid.poster = base + v.poster;
        fig.appendChild(vid);
        if (v.caption) {
          var cap = document.createElement("figcaption");
          cap.textContent = v.caption;
          fig.appendChild(cap);
        }
        grid.appendChild(fig);
      });
      box.hidden = false;
    })
    .catch(function () {});
})();
