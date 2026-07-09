import type { ReactNode } from "react";

import { SheetPanel } from "@/components/ui-system/SheetPanel";

type DrawerPanelProps = {
  open: boolean;
  title: string;
  children: ReactNode;
  onClose: () => void;
};

export function DrawerPanel(props: DrawerPanelProps) {
  return <SheetPanel side="right" {...props} />;
}

