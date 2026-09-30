"""
Ready-made smart playlists for the iPod, driven by play counts and ratings.

The iPod's own play counts are merged into its database on every sync, so these lists
follow what you actually listen to. They are evaluated here when they're written and
refreshed on the next sync.
"""

from i18n import N_, _

GREATER, IS = 0x00000010, 0x00000001
PLAYS, RATING, LENGTH = 0x16, 0x19, 0x0D
SONGS, MOST_OFTEN, MOST_RECENTLY_ADDED = 0x03, 0x14, 0x10


def _rule(field, action, value):
    from podsync.itdb.writer.smart_rules import SmartRule
    return SmartRule(field_id=field, action_id=action, from_value=value, to_value=value,
                     from_units=1, to_units=1)


def presets():
    """[(name, prefs, rules)]"""
    from podsync.itdb.writer.smart_rules import SmartPrefs, SmartRuleSet
    limited = dict(check_limits=True, limit_type=SONGS)
    return [
        (_(N_("Never played")), SmartPrefs(), SmartRuleSet(rules=[_rule(PLAYS, IS, 0)])),
        (_(N_("Most played")),
         SmartPrefs(**limited, limit_value=50, limit_sort=MOST_OFTEN),
         SmartRuleSet(rules=[_rule(PLAYS, GREATER, 0)])),
        (_(N_("Top rated")), SmartPrefs(), SmartRuleSet(rules=[_rule(RATING, GREATER, 60)])),
        (_(N_("Recently added")),
         SmartPrefs(**limited, limit_value=25, limit_sort=MOST_RECENTLY_ADDED),
         SmartRuleSet(rules=[_rule(LENGTH, GREATER, 0)])),
    ]


def build(records, existing_names):
    """Playlist records for the presets the iPod doesn't have yet, filled from records."""
    from podsync.itdb.writer.playlist import PlaylistRecord
    from podsync.library.smart import evaluate_smart_playlist
    from podsync.library.tracks import rule_view_of
    views = [rule_view_of(r) for r in records]
    out = []
    for name, prefs, rules in presets():
        if name not in existing_names:
            ids = evaluate_smart_playlist(prefs, rules, views)
            out.append(PlaylistRecord(name=name, track_ids=ids, smart_prefs=prefs, smart_rules=rules))
    return out
