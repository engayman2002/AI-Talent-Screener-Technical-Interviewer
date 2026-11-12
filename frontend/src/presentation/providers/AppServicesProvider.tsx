"use client";

import React, { createContext, useContext, useMemo, useRef } from "react";
import type { FrontendServicesBundle } from "../../composition/container";
import { createFrontendServices } from "../../composition/container";

const FrontendServicesContext = createContext<FrontendServicesBundle | null>(null);

export interface AppServicesProviderProps {
  readonly children: React.ReactNode;
  readonly apiBaseUrl?: string;
  readonly initialAuthToken?: string | null;
}

export function AppServicesProvider({
  children,
  apiBaseUrl = "/api",
  initialAuthToken = null,
}: AppServicesProviderProps): React.JSX.Element {
  const tokenRef = useRef<string | null>(initialAuthToken);

  const bundle = useMemo(
    () =>
      createFrontendServices({
        apiBaseUrl,
        getAuthToken: () => tokenRef.current,
      }),
    [apiBaseUrl],
  );

  return (
    <FrontendServicesContext.Provider value={bundle}>
      {children}
    </FrontendServicesContext.Provider>
  );
}

export function useFrontendServices(): FrontendServicesBundle {
  const ctx = useContext(FrontendServicesContext);
  if (ctx === null) {
    throw new Error("useFrontendServices must be used inside <AppServicesProvider>.");
  }
  return ctx;
}
