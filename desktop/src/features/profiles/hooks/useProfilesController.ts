import { useI18n } from "../../../shared/i18n";
import { useProfileEditor } from "./useProfileEditor";
import { useTrackingSources } from "./useTrackingSources";

export function useProfilesController() {
  const { l } = useI18n();
  const editor = useProfileEditor(l);
  const trackingSources = useTrackingSources({
    profile: editor.profile,
    update: editor.update,
  });

  return {
    l,
    ...editor,
    ...trackingSources,
  };
}
