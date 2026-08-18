
(function (global) {
  var COOKIE_NAME = 'stm_favorites';
  var MAX_AGE = 60 * 60 * 24 * 365;

  function getFavorites() {
    var match = document.cookie.match(new RegExp('(?:^|; )' + COOKIE_NAME + '=([^;]*)'));
    if (!match) return [];
    try { return JSON.parse(decodeURIComponent(match[1])); } catch (e) { return []; }
  }

  function setFavorites(list) {
    document.cookie = COOKIE_NAME + '=' + encodeURIComponent(JSON.stringify(list)) + '; max-age=' + MAX_AGE + '; path=/; samesite=lax';
  }

  function isFavorite(line, code) {
    return getFavorites().some(function (f) { return f.line === line && f.code === code; });
  }

  function toggleFavorite(stop) {
    var list = getFavorites();
    var idx = list.findIndex(function (f) { return f.line === stop.line && f.code === stop.code; });
    if (idx >= 0) { list.splice(idx, 1); } else { list.push(stop); }
    setFavorites(list);
    return idx < 0;
  }

  function removeFavorite(line, code) {
    setFavorites(getFavorites().filter(function (f) { return !(f.line === line && f.code === code); }));
  }

  function haversineKm(lat1, lon1, lat2, lon2) {
    var R = 6371;
    var dLat = (lat2 - lat1) * Math.PI / 180;
    var dLon = (lon2 - lon1) * Math.PI / 180;
    var a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
      Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
      Math.sin(dLon / 2) * Math.sin(dLon / 2);
    return 2 * R * Math.asin(Math.sqrt(a));
  }

  global.STM = {
    getFavorites: getFavorites,
    setFavorites: setFavorites,
    isFavorite: isFavorite,
    toggleFavorite: toggleFavorite,
    removeFavorite: removeFavorite,
    haversineKm: haversineKm,
  };
})(window);
