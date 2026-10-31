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
      add: (name) => classes.add(name),
      remove: (name) => classes.delete(name),
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
    attributes,
  };
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
    querySelectorAll: (selector) => selectors[selector] || [],
    addEventListener: () => {},
  };
}
