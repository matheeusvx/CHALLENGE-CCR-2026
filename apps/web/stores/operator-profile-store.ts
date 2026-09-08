import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import { mockUser } from "@/lib/app-config";

export type OperatorProfile = {
  name: string;
  email: string;
  role: string;
  organization: string;
  avatarDataUrl: string | null;
};

type OperatorProfileState = OperatorProfile & {
  updateProfile: (data: { name: string; email: string }) => void;
  setAvatar: (avatarDataUrl: string | null) => void;
  resetProfile: () => void;
};

export const DEFAULT_OPERATOR_PROFILE: OperatorProfile = {
  name: mockUser.name,
  email: mockUser.email,
  role: mockUser.role,
  organization: mockUser.organization,
  avatarDataUrl: null,
};

export const useOperatorProfileStore = create<OperatorProfileState>()(
  persist(
    (set) => ({
      ...DEFAULT_OPERATOR_PROFILE,
      updateProfile: ({ name, email }) => set({ name: name.trim(), email: email.trim() }),
      setAvatar: (avatarDataUrl) => set({ avatarDataUrl }),
      resetProfile: () => set({ ...DEFAULT_OPERATOR_PROFILE }),
    }),
    {
      name: "motiva.operator-profile",
      storage: createJSONStorage(() => localStorage),
      // Keep SSR and the first client render on DEFAULT_OPERATOR_PROFILE.
      // AppSidebar explicitly rehydrates this store after mount.
      skipHydration: true,
      partialize: (state) => ({
        name: state.name,
        email: state.email,
        role: state.role,
        organization: state.organization,
        avatarDataUrl: state.avatarDataUrl,
      }),
    },
  ),
);
