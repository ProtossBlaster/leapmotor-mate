/**
 * Exact-order JSON parser, flattener, and reconstructor.
 * Preserves master key order (including numeric-looking object keys)
 * across parsing, flattening, and final hr.json export.
 */
(function (global) {
  'use strict';

  function isWhitespace(ch) {
    return ch === ' ' || ch === '\t' || ch === '\n' || ch === '\r';
  }

  /**
   * Parse JSON text into an AST preserving exact object key order.
   * Objects become { __type: 'object', entries: Array<[string, any]> }.
   * Arrays become  { __type: 'array', items: Array<any> }.
   */
  function parseOrderedJson(text) {
    if (typeof text !== 'string') {
      throw new Error('JSON input must be a string.');
    }
    // Strip optional UTF-8 BOM
    const src = text.charCodeAt(0) === 0xfeff ? text.slice(1) : text;
    let i = 0;
    const len = src.length;

    function skipWs() {
      while (i < len && isWhitespace(src[i])) i++;
    }

    function fail(msg) {
      const snippet = src.slice(Math.max(0, i - 16), Math.min(len, i + 16));
      throw new Error(`${msg} at position ${i} (near ${JSON.stringify(snippet)})`);
    }

    function parseString() {
      if (src[i] !== '"') fail('Expected string');
      const start = i;
      i++; // opening quote
      while (i < len) {
        const ch = src[i];
        if (ch === '\\') {
          i += 2;
          continue;
        }
        if (ch === '"') {
          i++;
          return JSON.parse(src.slice(start, i));
        }
        i++;
      }
      fail('Unterminated string');
    }

    function parseLiteral(literal, value) {
      if (src.slice(i, i + literal.length) !== literal) {
        fail(`Expected ${literal}`);
      }
      i += literal.length;
      return value;
    }

    function parseNumber() {
      const start = i;
      if (src[i] === '-') i++;
      while (i < len && src[i] >= '0' && src[i] <= '9') i++;
      if (src[i] === '.') {
        i++;
        while (i < len && src[i] >= '0' && src[i] <= '9') i++;
      }
      if (src[i] === 'e' || src[i] === 'E') {
        i++;
        if (src[i] === '+' || src[i] === '-') i++;
        while (i < len && src[i] >= '0' && src[i] <= '9') i++;
      }
      const slice = src.slice(start, i);
      const num = Number(slice);
      if (!slice || Number.isNaN(num)) fail('Invalid number');
      return num;
    }

    function parseObject() {
      i++; // '{'
      skipWs();
      const entries = [];
      if (src[i] === '}') {
        i++;
        return { __type: 'object', entries };
      }
      while (i < len) {
        skipWs();
        if (src[i] !== '"') fail('Expected object key');
        const key = parseString();
        skipWs();
        if (src[i] !== ':') fail('Expected ":" after object key');
        i++;
        const value = parseValue();
        entries.push([key, value]);
        skipWs();
        if (src[i] === ',') {
          i++;
          continue;
        }
        if (src[i] === '}') {
          i++;
          return { __type: 'object', entries };
        }
        fail('Expected "," or "}" in object');
      }
      fail('Unterminated object');
    }

    function parseArray() {
      i++; // '['
      skipWs();
      const items = [];
      if (src[i] === ']') {
        i++;
        return { __type: 'array', items };
      }
      while (i < len) {
        items.push(parseValue());
        skipWs();
        if (src[i] === ',') {
          i++;
          continue;
        }
        if (src[i] === ']') {
          i++;
          return { __type: 'array', items };
        }
        fail('Expected "," or "]" in array');
      }
      fail('Unterminated array');
    }

    function parseValue() {
      skipWs();
      if (i >= len) fail('Unexpected end of JSON');
      const ch = src[i];
      if (ch === '{') return parseObject();
      if (ch === '[') return parseArray();
      if (ch === '"') return parseString();
      if (ch === 't') return parseLiteral('true', true);
      if (ch === 'f') return parseLiteral('false', false);
      if (ch === 'n') return parseLiteral('null', null);
      if (ch === '-' || (ch >= '0' && ch <= '9')) return parseNumber();
      fail(`Unexpected token "${ch}"`);
    }

    const result = parseValue();
    skipWs();
    if (i < len) fail('Unexpected trailing characters');
    return result;
  }

  function isOrderedObject(node) {
    return Boolean(node && typeof node === 'object' && node.__type === 'object');
  }

  function isOrderedArray(node) {
    return Boolean(node && typeof node === 'object' && node.__type === 'array');
  }

  function formatDisplayPath(segments) {
    let out = '';
    for (let i = 0; i < segments.length; i++) {
      const seg = segments[i];
      if (typeof seg === 'number') {
        out += `[${seg}]`;
      } else if (out === '') {
        out = seg;
      } else {
        out += `.${seg}`;
      }
    }
    return out;
  }

  /**
   * Flatten an ordered JSON AST into leaf string entries in exact traversal order.
   */
  function flattenMasterStrings(ast) {
    const leaves = [];

    function walk(node, segments) {
      if (isOrderedObject(node)) {
        for (let idx = 0; idx < node.entries.length; idx++) {
          const [key, val] = node.entries[idx];
          walk(val, segments.concat([key]));
        }
      } else if (isOrderedArray(node)) {
        for (let idx = 0; idx < node.items.length; idx++) {
          walk(node.items[idx], segments.concat([idx]));
        }
      } else if (typeof node === 'string') {
        const group = segments.length > 1 ? formatDisplayPath(segments.slice(0, -1)) : 'root';
        const lastSeg = segments[segments.length - 1];
        const shortKey = typeof lastSeg === 'number'
          ? `${segments[segments.length - 2] ?? 'item'}[${lastSeg}]`
          : String(lastSeg);
        leaves.push({
          path: segments,
          pathId: JSON.stringify(segments),
          displayKey: formatDisplayPath(segments),
          group,
          shortKey,
          enValue: node,
        });
      }
    }

    walk(ast, []);
    return leaves;
  }

  /**
   * Build a lookup Map<pathId, string> from an ordered JSON AST.
   */
  function buildLeafLookup(ast) {
    const map = new Map();
    function walk(node, segments) {
      if (isOrderedObject(node)) {
        for (let idx = 0; idx < node.entries.length; idx++) {
          const [key, val] = node.entries[idx];
          walk(val, segments.concat([key]));
        }
      } else if (isOrderedArray(node)) {
        for (let idx = 0; idx < node.items.length; idx++) {
          walk(node.items[idx], segments.concat([idx]));
        }
      } else if (typeof node === 'string') {
        map.set(JSON.stringify(segments), node);
      }
    }
    walk(ast, []);
    return map;
  }

  /**
   * Serialize an ordered AST back to formatted JSON (2-space indent + trailing newline),
   * replacing leaf strings from `valueByPathId` while keeping master key order intact.
   */
  function serializeOrderedJson(ast, valueByPathId, indentSize = 2) {
    const indentUnit = ' '.repeat(indentSize);

    function render(node, segments, depth) {
      if (isOrderedObject(node)) {
        if (node.entries.length === 0) return '{}';
        const pad = indentUnit.repeat(depth + 1);
        const closePad = indentUnit.repeat(depth);
        const lines = node.entries.map(([key, val]) => {
          const renderedVal = render(val, segments.concat([key]), depth + 1);
          return `${pad}${JSON.stringify(key)}: ${renderedVal}`;
        });
        return `{\n${lines.join(',\n')}\n${closePad}}`;
      }

      if (isOrderedArray(node)) {
        if (node.items.length === 0) return '[]';
        const pad = indentUnit.repeat(depth + 1);
        const closePad = indentUnit.repeat(depth);
        const lines = node.items.map((item, idx) => {
          return `${pad}${render(item, segments.concat([idx]), depth + 1)}`;
        });
        return `[\n${lines.join(',\n')}\n${closePad}]`;
      }

      if (typeof node === 'string') {
        const pid = JSON.stringify(segments);
        const str = valueByPathId && valueByPathId.has(pid) ? valueByPathId.get(pid) : node;
        return JSON.stringify(str);
      }

      return JSON.stringify(node);
    }

    return render(ast, [], 0) + '\n';
  }

  /**
   * Extract placeholder tokens like {time}, {pct}, {{var}}, %s for translation QA hints.
   */
  function extractPlaceholders(text) {
    if (typeof text !== 'string' || !text) return [];
    const matches = text.match(/\{\{[^{}]+\}\}|\{[^{}\s]+\}|%[sd]/g);
    if (!matches) return [];
    return Array.from(new Set(matches));
  }

  /**
   * Compare placeholders between English source and Croatian translation.
   */
  function checkPlaceholders(enText, hrText) {
    const expected = extractPlaceholders(enText);
    if (expected.length === 0) {
      return { expected: [], missing: [], extra: [] };
    }
    const found = extractPlaceholders(hrText || '');
    const foundSet = new Set(found);
    const expectedSet = new Set(expected);
    return {
      expected,
      missing: expected.filter((tok) => !foundSet.has(tok)),
      extra: found.filter((tok) => !expectedSet.has(tok)),
    };
  }

  global.OrderedJson = {
    parseOrderedJson,
    flattenMasterStrings,
    buildLeafLookup,
    serializeOrderedJson,
    extractPlaceholders,
    checkPlaceholders,
  };
})(window);
