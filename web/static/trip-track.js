/* Trip routes as every map draws them: one trip (its page) in red, many trips (the Map, the report's
   month) in a thinner blue, so overlapping routes stay legible. A white casing under each line keeps it
   readable over any road colour (OSM motorways and primaries are red, orange and yellow). A gap-bridge
   run (real signal loss — see db_reader._split_track_gaps) gets its own white casing too, then a bold
   magenta dash on top — a colour used nowhere else on these maps (not the route red or blue, not OSM's
   own grey/brown dashed tracks and borders), wide enough to read as a deliberate marking instead of
   blending into the base tiles.

   tripTrack(segments)   // one trip: [{ points: [[lat, lon], ...], gap }] -> an L.layerGroup, not yet on a map
   tripTracks(tracks)    // many trips, one segments list each, never joined -> an L.layerGroup */
(function () {
  // The lines a solid run and a gap bridge are drawn with, casing first.
  const ONE = {
    solid: [{ color: '#ffffff', weight: 8, opacity: 0.7 },
            { color: '#e63946', weight: 5, opacity: 1.0 }],
    gap: [{ color: '#ffffff', weight: 6, opacity: 0.6, lineCap: 'round' },
          { color: '#ec4899', weight: 3.5, opacity: 0.95, dashArray: '10,8', lineCap: 'round' }]
  };
  const MANY = {
    solid: [{ color: '#ffffff', weight: 5, opacity: 0.7, lineJoin: 'round', lineCap: 'round' },
            { color: '#2563eb', weight: 2.5, opacity: 0.9, lineJoin: 'round', lineCap: 'round' }],
    gap: [{ color: '#ffffff', weight: 5, opacity: 0.6, lineCap: 'round' },
          { color: '#ec4899', weight: 3, opacity: 0.95, dashArray: '10,8', lineCap: 'round' }]
  };

  function draw(group, segments, look) {
    segments.forEach(function (run) {
      (run.gap ? look.gap : look.solid).forEach(function (line) { L.polyline(run.points, line).addTo(group); });
    });
    return group;
  }

  window.tripTrack = function (segments) { return draw(L.layerGroup(), segments, ONE); };
  window.tripTracks = function (tracks) {
    const group = L.layerGroup();
    tracks.forEach(function (segments) { draw(group, segments, MANY); });
    return group;
  };
})();
