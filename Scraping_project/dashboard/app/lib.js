// One import point for the vendored Preact, hooks and htm.
import { h, render, Fragment, createContext } from '../vendor/preact.module.js';
import {
  useState, useEffect, useLayoutEffect, useRef, useMemo, useCallback, useContext, useId,
} from '../vendor/hooks.module.js';
import htm from '../vendor/htm.module.js';

export const html = htm.bind(h);
export {
  h, render, Fragment, createContext,
  useState, useEffect, useLayoutEffect, useRef, useMemo, useCallback, useContext, useId,
};
