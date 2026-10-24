import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

export type ThemeChoice = "light" | "dark" | "system";
export type Theme = "light" | "dark";

const KEY = "ia_theme";

export function themeChoice(): ThemeChoice {
  const saved = localStorage.getItem(KEY);
  if (saved === "light" || saved === "dark" || saved === "system") return saved;
  return "system";
}

export function resolveTheme(choice: ThemeChoice = themeChoice()): Theme {
  if (choice === "light" || choice === "dark") return choice;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function applyTheme(choice: ThemeChoice) {
  const resolved = resolveTheme(choice);
  document.documentElement.dataset.theme = resolved;
  document.documentElement.style.colorScheme = resolved;
  localStorage.setItem(KEY, choice);
}

const ThemeContext = createContext<{
  choice: ThemeChoice;
  resolved: Theme;
  setChoice: (choice: ThemeChoice) => void;
}>({
  choice: "system",
  resolved: "light",
  setChoice: () => undefined,
});

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [choice, setChoiceState] = useState<ThemeChoice>(themeChoice);
  const [resolved, setResolved] = useState<Theme>(resolveTheme);

  function setChoice(next: ThemeChoice) {
    applyTheme(next);
    setChoiceState(next);
    setResolved(resolveTheme(next));
  }

  useEffect(() => {
    applyTheme(choice);
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      if (themeChoice() !== "system") return;
      applyTheme("system");
      setResolved(resolveTheme("system"));
    };
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, [choice]);

  return <ThemeContext.Provider value={{ choice, resolved, setChoice }}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  return useContext(ThemeContext);
}
