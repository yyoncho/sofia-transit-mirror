
(function (global) {
  var USER_ID_KEY = 'stm_user_id';

  function getUserId() {
    try {
      var id = localStorage.getItem(USER_ID_KEY);
      if (!id) {
        id = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2));
        localStorage.setItem(USER_ID_KEY, id);
      }
      return id;
    } catch (e) {
      // storage unavailable (private mode etc.) — fall back to a per-tab id
      if (!global.__stmSessionId) global.__stmSessionId = String(Date.now()) + Math.random().toString(16).slice(2);
      return global.__stmSessionId;
    }
  }

  function getFavorites() {
    return fetch('/api/favorites?user_id=' + encodeURIComponent(getUserId()))
      .then(function (r) { if (!r.ok) throw new Error('bad status'); return r.json(); });
  }

  function isFavorite(line, code, list) {
    return list.some(function (f) { return f.line === line && f.code === code; });
  }

  function addFavorite(stop) {
    return fetch('/api/favorites', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(Object.assign({ user_id: getUserId() }, stop)),
    });
  }

  function removeFavorite(line, code) {
    var params = new URLSearchParams({ user_id: getUserId(), line: line, code: code });
    return fetch('/api/favorites?' + params.toString(), { method: 'DELETE' });
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
    getUserId: getUserId,
    getFavorites: getFavorites,
    isFavorite: isFavorite,
    addFavorite: addFavorite,
    removeFavorite: removeFavorite,
    haversineKm: haversineKm,
  };
})(window);
