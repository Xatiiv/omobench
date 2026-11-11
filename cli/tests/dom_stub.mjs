// A minimal DOM stand-in shared by the panel harnesses, so the guided page and
// the advanced page are driven through the same stub rather than two drifting
// copies of one. It is deliberately tiny: enough for the real page scripts to
// run unmodified in a VM context, and nothing that would quietly emulate
// browser behaviour the pages must not depend on.

export function makeElement(id) {
  const classes = new Set();
  // The chart is hidden until a real sample exists, exactly as the markup ships.
  const attributes = id === "chart-svg" ? { hidden: "" } : {};
  const listeners = {};
  return {
    id,
    textContent: "",
    innerHTML: "",
    hidden: false,
    disabled: false,
    checked: false,
    required: false,
    value: "",
    children: [],
    dataset: {},
    style: {},
    scrollTop: 0,
    scrollHeight: 0,
    clientHeight: 0,
    classList: {
      add: (...names) => names.forEach((name) => classes.add(name)),
      remove: (...names) => names.forEach((name) => classes.delete(name)),
      toggle: (name, force) => (force === undefined ? (classes.has(name) ? classes.delete(name) : classes.add(name)) : force ? classes.add(name) : classes.delete(name)),
      contains: (name) => classes.has(name),
    },
    setAttribute: (name, v) => { attributes[name] = String(v); },
    getAttribute: (name) => (name in attributes ? attributes[name] : null),
    removeAttribute: (name) => { delete attributes[name]; },
    addEventListener: (name, handler) => { listeners[name] = handler; },
    trigger: (name, event) => listeners[name]?.(event),
    append(...items) { this.children.push(...items); },
    replaceChildren(...items) { this.children = items; },
    scrollIntoView: () => {},
    focus: () => {},
    reset: () => {},
    querySelectorAll: () => [],
    querySelector: () => null,
    classes,
    attributes,
  };
}

// `.step-tab[data-stage="run"]` is how a page addresses one member of a
// registered group. Resolving it here -- rather than making every harness
// register each combination -- is what lets the stepper be exercised at all.
function matching(selectors, selector) {
  if (selectors[selector]) return selectors[selector];
  const attribute = /^(.*)\[data-([a-z-]+)="([^"]*)"\]$/.exec(selector);
  if (!attribute) return [];
  const [, base, name, value] = attribute;
  const key = name.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
  return (selectors[base] || []).filter((node) => node.dataset[key] === value);
}

export function makeDocument({ selectors = {} } = {}) {
  const elements = new Map();
  return {
    elements,
    getElementById: (id) => {
      if (!elements.has(id)) elements.set(id, makeElement(id));
      return elements.get(id);
    },
    createElement: (tag) => makeElement(`created:${tag}`),
    querySelectorAll: (selector) => matching(selectors, selector),
    // Returns null for a selector nothing matches, exactly as a browser does,
    // so a page that guards on the result is exercised rather than crashing.
    querySelector: (selector) => matching(selectors, selector)[0] ?? null,
    addEventListener: () => {},
  };
}
