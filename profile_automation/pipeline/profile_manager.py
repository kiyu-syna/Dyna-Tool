from core.config import load_profile_configs

class ProfileManager:
    @staticmethod
    def get_active_profiles() -> list:
        """
        Retrieve all active (enabled=True) profiles from the configuration directory.
        """
        all_profiles = load_profile_configs()
        active = []
        for p_id, cfg in all_profiles.items():
            if cfg.get("enabled", True):
                active.append(cfg)
        return active
