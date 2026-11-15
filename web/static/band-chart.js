/* One chart in bands on one time axis: each band holds the lines named for it, on their own scales,
   with one cursor and one hover box across all of them. A band is five ticks tall: its lines use the
   lower four, the fifth is an empty gap above them. Labels print on the side a band names, three to a
   band, in the colour of the line they measure, the unit at the top.

   The legend's buttons ([data-series="<id>"], a [data-swatch] inside) switch their line on and off;
   an empty square is a line switched off. A band with every line off folds away, and the choice is
   kept in localStorage under `storageKey`. The ApexCharts instance is left on `el._c`.

   mateBandChart(el, {
     labels,            // the x categories
     lines,             // id -> { name, color, data, unit, dec, scale: [lo, hi], axisDec: decimals the
                        //         scale prints with (0 when absent; niceScale's `dec` must match),
                        //         rangeHover: the hover box gives "data – dashed" (lowest – highest),
                        //         dots: a reading with no neighbour drawn as a dot,
                        //         dashed: data drawn dashed on the same scale and kept out of the
                        //         hover box, zeroLine: a dashed rule at the line's zero }
                        // lines of a band that share a scale are given the same array
     bands,             // [{ ids, left, right }]: the lines of a band, and whose scale prints on each
                        // side; a shared scale prints while any line on it shows
     legend,            // the element holding the buttons
     storageKey,        // where the browser remembers which lines show
     shownByDefault,    // id -> bool for a line never switched (every line, when absent)
     hoverTitle(i),     // the heading of the hover box at category i
     decimalComma,      // numbers written with a comma, as units.decimal_point does for the language
                        // (opt-in: the charts that do not ask keep the dot they always had)
     annotations,       // ApexCharts xaxis annotations, if any
     onHover(i), onLeave()
   })
   Helpers for the page: mateBandChart.spread (the slots of a time axis, blanks for a hole in the
   recording), and for the scales it computes, .niceScale, .extent and .present. */
(function () {
  // Four equal steps of a round size covering [lo, hi], so a band's inner ticks read as round
  // numbers and print exactly (a step of 2.5 would print its line at 62.5 as 63). Whole steps, or with
  // `dec` steps that print exactly at that many decimals — a tyre at 2.40–2.55 bar has no whole step
  // that is not the whole chart (D#483). The line's `axisDec` must then say the same `dec`.
  function niceScale(lo, hi, minSpan, dec) {
    // Worked in units of the last decimal, snapped: 2.3 - 2.1 is 0.1999… in floating point, and a
    // span a hair short of the least one, or a top a hair over a tick, doubled the scale.
    var unit = Math.pow(10, dec || 0);
    function snap(v) { return Math.round(v * unit * 1e6) / 1e6; }
    var L = snap(lo), H = snap(hi), M = snap(minSpan);
    if (H - L < M - 1e-6) { var mid = (L + H) / 2; L = mid - M / 2; H = mid + M / 2; }
    var mag = Math.pow(10, Math.floor(Math.log10((H - L) / 4)));
    var steps = [1, 2, 2.5, 3, 4, 5, 10, 20].map(function (m) { return Math.round(m * mag * 1e6) / 1e6; })
                                             .filter(function (s) { return s % 1 === 0 && s > 0; });
    for (var s = 0; s < steps.length; s++) {
      var a = Math.floor(L / steps[s] + 1e-9) * steps[s];
      if (a + 4 * steps[s] >= H - 1e-6) return [a / unit, (a + 4 * steps[s]) / unit];
    }
    return [lo, hi];
  }
  function extent(arr) {
    var v = arr.filter(function (x) { return x != null; });
    return [Math.min.apply(null, v), Math.max.apply(null, v)];
  }
  function present(arr) { return arr.some(function (v) { return v != null; }); }

  // Samples on a categorical axis take equal slots whatever the time between them, so a hole in the
  // recording would silently close up. The slots to draw are the samples' instants with blanks for
  // every gap wider than three typical steps, the typical step being the median spacing, so the gaps
  // looked for do not skew the very yardstick meant to find them. `at[k]` is the sample slot k stands
  // for, null for a blank; an instant that is NaN never opens a gap.
  function spread(ms) {
    var deltas = [];
    for (var j = 1; j < ms.length; j++) { var d = ms[j] - ms[j - 1]; if (d > 0) deltas.push(d); }
    deltas.sort(function (a, b) { return a - b; });
    var step = deltas.length ? deltas[Math.floor(deltas.length / 2)] : 0;
    var slots = [], at = [];
    ms.forEach(function (t, i) {
      if (step > 0 && i > 0 && !isNaN(t) && !isNaN(ms[i - 1]) && t - ms[i - 1] > step * 3) {
        var blanks = Math.min(Math.round((t - ms[i - 1]) / step) - 1, 300);   // sanity cap
        for (var k = 1; k <= blanks; k++) { slots.push(ms[i - 1] + step * k); at.push(null); }
      }
      slots.push(t); at.push(i);
    });
    return { slots: slots, at: at };
  }
  function esc(text) { return String(text).replace(/[&<>"]/g, function (ch) { return '&#' + ch.charCodeAt(0) + ';'; }); }

  function mateBandChart(el, opts) {
    var C = opts.lines, BANDS = opts.bands, labels = opts.labels;
    var onHover = opts.onHover || function () {}, onLeave = opts.onLeave || function () {};
    function fix(v, dec) { var s = v.toFixed(dec); return opts.decimalComma ? s.replace('.', ',') : s; }
    Object.keys(C).forEach(function (id) { C[id].present = present(C[id].data); });

    // Which lines show is remembered in this browser; one never switched keeps its default.
    var shown = {};
    try { shown = JSON.parse(localStorage.getItem(opts.storageKey)) || {}; } catch (e) { shown = {}; }
    function isShown(id) {
      var byDefault = opts.shownByDefault ? !!opts.shownByDefault[id] : true;
      return C[id].present && (id in shown ? !!shown[id] : byDefault);
    }

    // The hover box: the heading, then the lines band by band, a rule between the bands.
    function hoverBox(bands, i) {
      var html = '<div class="apexcharts-tooltip-title" style="font-size:12px">' + esc(opts.hoverTitle(i)) + '</div>';
      bands.forEach(function (b, n) {
        var rows = b.ids.filter(isShown).map(function (id) {
          var c = C[id], v = c.data[i];
          var value = v == null ? '—' : fix(v, c.dec) + (c.unit === '%' ? '' : ' ') + c.unit;   // "76.0%", like the rest of Mate
          // A line drawn as a range (its lowest solid, its highest dashed) gives both: "2.40 – 2.55 bar".
          var w = c.rangeHover && c.dashed ? c.dashed[i] : null;
          if (v != null && w != null && fix(w, c.dec) !== fix(v, c.dec)) {
            value = fix(v, c.dec) + ' – ' + fix(w, c.dec) + (c.unit === '%' ? '' : ' ') + c.unit;
          }
          return '<div style="display:flex;align-items:center;gap:6px;padding:2px 10px;font-size:12px">'
            + '<span style="width:9px;height:9px;border-radius:50%;background:' + c.color + '"></span>'
            + '<span>' + esc(c.name) + ':</span><b style="margin-left:auto;padding-left:10px">' + esc(value) + '</b></div>';
        });
        if (rows.length) {
          html += '<div class="mate-tip-band" style="padding:4px 0;' + (n ? 'border-top:1px solid #475569' : '') + '">' + rows.join('') + '</div>';
        }
      });
      return html;
    }

    function render() {
      if (el._c) { try { el._c.destroy(); } catch (e) {} el._c = null; }
      // A band with every line switched off folds away; the rest share the height.
      var bands = BANDS.filter(function (b) { return b.ids.some(isShown); });
      var nb = bands.length;
      el.style.display = nb ? '' : 'none';
      if (!nb) { onLeave(); return; }
      bands.forEach(function (b, i) { b.base = nb - 1 - i; });   // band 0 is the bottom one
      var FILL = 0.8;
      function toBand(c, base) {
        return function (v) { return v != null ? base + FILL * (v - c.scale[0]) / (c.scale[1] - c.scale[0]) : null; };
      }
      // The line a side's numbers measure. A scale shared by lines of a band (given one array) keeps its
      // numbers while any of them shows, in the colour of the first that does.
      function lineAt(side, q) {
        var b = bands.filter(function (x) { return x.base === Math.floor(q / 5); })[0];
        var id = b && b[side];
        if (!id || !(q % 5)) return null;
        return b.ids.filter(function (x) { return isShown(x) && C[x].scale === C[id].scale; })[0] || null;
      }
      function labelColors(side) {   // each band's numbers in the colour of the line they measure
        var out = [];
        for (var q = 0; q <= 5 * nb; q++) out.push(lineAt(side, q) ? C[lineAt(side, q)].color : '#94a3b8');
        return out;
      }
      function label(side) {   // three numbers up a band's scale, its unit at the top
        return function (t) {
          var q = Math.round(t * 5), id = lineAt(side, q);
          if (!id) return '';
          var c = C[id], k = q % 5;
          return k < 4 ? (c.scale[0] + k / 4 * (c.scale[1] - c.scale[0])).toFixed(c.axisDec || 0).replace('.', opts.decimalComma ? ',' : '.') : c.unit;
        };
      }
      // Two carrier series hold the labelled axes, so switching a line off never moves the plot.
      var axisRange = { min: 0, max: nb, tickAmount: 5 * nb };
      // As many time labels as the axis is wide for, up to six intervals: the library leaves labels that run into
      // each other where they are on this axis, and a phone's chart is a third as wide as a computer's. The axis is
      // the chart's width less the two scales' columns and the padding (122 px); it may print one label more than
      // the intervals asked for, so each label is given an interval of its own width and 12 px.
      var longest = labels.reduce(function (a, l) { return String(l).length > a.length ? String(l) : a; }, '');
      var pen = render.pen || (render.pen = document.createElement('canvas').getContext('2d'));
      pen.font = '10px ' + getComputedStyle(el).fontFamily;
      var xTicks = Math.max(1, Math.min(6, Math.floor(((el.clientWidth || 300) - 122) / (pen.measureText(longest).width + 12)) - 1));
      var series = [{ name: '_left', type: 'line', data: labels.map(function () { return null; }) },
                    { name: '_right', type: 'line', data: labels.map(function () { return null; }) }];
      var colors = ['transparent', 'transparent'], widths = [0, 0], dashes = [0, 0];
      var yaxis = [
        Object.assign({ seriesName: '_left', labels: { minWidth: 28, maxWidth: 28, style: { colors: labelColors('left'), fontSize: '10px' }, formatter: label('left') } }, axisRange),
        Object.assign({ seriesName: '_right', opposite: true, labels: { minWidth: 28, maxWidth: 28, style: { colors: labelColors('right'), fontSize: '10px' }, formatter: label('right') } }, axisRange),
      ];
      var tipSeries = [], lines = [], dots = [];
      // A line asking for `dots` gets one on each reading with no neighbour: a line needs two points,
      // and a day alone between two holes would otherwise not be drawn at all (D#483).
      function dotsFor(c, arr, at) {
        if (!c.dots || !arr) return;
        arr.forEach(function (v, i) {
          if (v != null && (i === 0 || arr[i - 1] == null) && (i === arr.length - 1 || arr[i + 1] == null)) {
            dots.push({ seriesIndex: at, dataPointIndex: i, fillColor: c.color, strokeColor: c.color, size: 3 });
          }
        });
      }
      bands.forEach(function (b) {
        b.ids.forEach(function (id) {
          if (!isShown(id)) return;
          var c = C[id], f = toBand(c, b.base);
          tipSeries.push(series.length);
          dotsFor(c, c.data, series.length);
          series.push({ name: c.name, type: 'line', data: c.data.map(f) });
          colors.push(c.color); widths.push(2); dashes.push(0);
          yaxis.push(Object.assign({ seriesName: c.name, show: false }, axisRange));
          if (c.dashed) {   // same scale, dashed, kept out of the hover box
            dotsFor(c, c.dashed, series.length);
            series.push({ name: c.name + '__dash', type: 'line', data: c.dashed.map(f) });
            colors.push(c.color); widths.push(2); dashes.push(4);
            yaxis.push(Object.assign({ seriesName: c.name + '__dash', show: false }, axisRange));
          }
          if (c.zeroLine) {
            lines.push({ y: f(0), yAxisIndex: 0, borderColor: c.color, strokeDashArray: 3, opacity: 0.6 });
          }
        });
      });

      el._c = new ApexCharts(el, {
        chart: { type: 'line', height: 36 + 125 * nb, toolbar: { show: false }, zoom: { enabled: false },
                 fontFamily: 'inherit', background: 'transparent', parentHeightOffset: 0,
                 animations: { enabled: false },
                 events: {
                   mouseMove: function (event, ctx, config) { onHover(config.dataPointIndex); },
                   mouseLeave: function () { onLeave(); }
                 } },
        series: series,
        colors: colors,
        stroke: { curve: 'smooth', width: widths, dashArray: dashes },
        fill: { type: 'solid', opacity: 1 },
        dataLabels: { enabled: false },
        markers: { size: 0, discrete: dots },
        annotations: { xaxis: opts.annotations || [], yaxis: lines },
        xaxis: { categories: labels, tickAmount: xTicks, tooltip: { enabled: false },
                 labels: { style: { colors: '#64748b', fontSize: '10px' }, rotate: 0, hideOverlappingLabels: true },
                 axisBorder: { show: false }, axisTicks: { show: false } },
        yaxis: yaxis,
        // Grid rows from the top, five a band: the gap, then the band's four rows as a darker box.
        grid: { borderColor: '#1e293b', strokeDashArray: 4, padding: { left: 8, right: 8 },
                row: { colors: ['transparent', '#0f172a', '#0f172a', '#0f172a', '#0f172a'], opacity: 1 } },
        legend: { show: false },
        tooltip: { theme: 'dark', shared: true, intersect: false, enabledOnSeries: tipSeries,
                   custom: function (o) { return hoverBox(bands, o.dataPointIndex); } },
      });
      el._c.render();
    }

    function paintLegend(id) {
      var b = opts.legend.querySelector('[data-series="' + id + '"]');
      if (C[id].present && b) b.style.display = '';
      if (!b) return;
      b.setAttribute('aria-pressed', isShown(id) ? 'true' : 'false');
      b.querySelector('[data-swatch]').textContent = isShown(id) ? '■' : '□';
    }
    Object.keys(C).forEach(function (id) {
      if (!C[id].present) return;
      paintLegend(id);
      opts.legend.querySelector('[data-series="' + id + '"]').onclick = function () {
        shown[id] = !isShown(id);
        // The browser's copy takes this one change and keeps the rest: another chart on the page may have
        // saved since this one started. This chart's own state is `shown`, whatever the storage does.
        try {
          var saved = JSON.parse(localStorage.getItem(opts.storageKey)) || {};
          saved[id] = shown[id];
          localStorage.setItem(opts.storageKey, JSON.stringify(saved));
        } catch (e) {}
        paintLegend(id);
        render();
      };
    });
    render();
    return { render: render, isShown: isShown };
  }

  mateBandChart.niceScale = niceScale;
  mateBandChart.extent = extent;
  mateBandChart.present = present;
  mateBandChart.spread = spread;
  window.mateBandChart = mateBandChart;
})();
