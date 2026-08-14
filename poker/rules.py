from __future__ import annotations

from typing import Any
from poker.models import (
    Card,
    Player,
    Street,
    LegalActionSummary,
    RaisePreset,
    _RANKS,
)


_CARD_VALUES = {
    "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9,
    "10": 10, "T": 10, "J": 11, "Q": 12, "K": 13, "A": 14
}

_RANK_NAMES_EN = {
    14: "Ace", 13: "King", 12: "Queen", 11: "Jack", 10: "Ten",
    9: "Nine", 8: "Eight", 7: "Seven", 6: "Six", 5: "Five",
    4: "Four", 3: "Three", 2: "Two"
}

_RANK_NAMES_ZH = {
    14: "A", 13: "K", 12: "Q", 11: "J", 10: "10",
    9: "9", 8: "8", 7: "7", 6: "6", 5: "5",
    4: "4", 3: "3", 2: "2"
}


def parse_card(card_str: str) -> tuple[int, str]:
    """Return (rank_value, suit) from a card string like 'A♠' or '10♦'."""
    if not isinstance(card_str, str) or len(card_str) < 2:
        raise ValueError(f"Invalid card string: {card_str}")
    suit = card_str[-1]
    rank_char = card_str[:-1].upper()
    val = _CARD_VALUES.get(rank_char)
    if val is None:
        raise ValueError(f"Invalid card rank: {rank_char}")
    return val, suit


class HandEvaluator:
    """Pure 5-to-7 card evaluator for Texas Hold'em hands."""

    @staticmethod
    def rank_hand(cards: list[str]) -> tuple[int, list[int]]:
        """Rank a 5-7 card hand. Returns (hand_type_rank, tie_breakers).

        Hand type ranks:
          8: Straight Flush (includes Royal Flush)
          7: Four of a Kind
          6: Full House
          5: Flush
          4: Straight
          3: Three of a Kind
          2: Two Pair
          1: One Pair
          0: High Card
        """
        if len(cards) < 5:
            vals = sorted([parse_card(c)[0] for c in cards], reverse=True)
            return (0, vals)

        parsed = [parse_card(c) for c in cards]
        vals = sorted([p[0] for p in parsed], reverse=True)
        suits = [p[1] for p in parsed]

        # 1. Flush check
        suit_counts: dict[str, int] = {}
        for s in suits:
            suit_counts[s] = suit_counts.get(s, 0) + 1
        flush_suit = next((s for s, count in suit_counts.items() if count >= 5), None)
        flush_vals = (
            sorted([v for v, s in parsed if s == flush_suit], reverse=True)
            if flush_suit
            else []
        )

        # 2. Straight check helper
        def get_straight_high(values: list[int]) -> int:
            unique = set(values)
            for high in range(14, 4, -1):
                if all(val in unique for val in range(high - 4, high + 1)):
                    return high
            # Wheel straight check (A-2-3-4-5) -> 5 high
            if {14, 2, 3, 4, 5}.issubset(unique):
                return 5
            return 0

        # Straight Flush / Royal Flush
        if flush_suit:
            sf_high = get_straight_high(flush_vals)
            if sf_high > 0:
                return (8, [sf_high])

        # Value frequencies
        freq: dict[int, int] = {}
        for v in vals:
            freq[v] = freq.get(v, 0) + 1

        quads = [v for v, count in freq.items() if count == 4]
        trips = sorted([v for v, count in freq.items() if count == 3], reverse=True)
        pairs = sorted([v for v, count in freq.items() if count == 2], reverse=True)

        # Four of a kind
        if quads:
            quad_val = quads[0]
            kicker = max((v for v in vals if v != quad_val), default=0)
            return (7, [quad_val, kicker])

        # Full House
        if trips and (len(trips) >= 2 or pairs):
            trips_val = trips[0]
            pair_val = trips[1] if len(trips) >= 2 else pairs[0]
            return (6, [trips_val, pair_val])

        # Flush
        if flush_suit:
            return (5, flush_vals[:5])

        # Straight
        straight_high = get_straight_high(vals)
        if straight_high > 0:
            return (4, [straight_high])

        # Three of a Kind
        if trips:
            trips_val = trips[0]
            kickers = [v for v in vals if v != trips_val][:2]
            return (3, [trips_val] + kickers)

        # Two Pair
        if len(pairs) >= 2:
            high_pair = pairs[0]
            low_pair = pairs[1]
            kicker = max((v for v in vals if v != high_pair and v != low_pair), default=0)
            return (2, [high_pair, low_pair, kicker])

        # One Pair
        if len(pairs) == 1:
            pair_val = pairs[0]
            kickers = [v for v in vals if v != pair_val][:3]
            return (1, [pair_val] + kickers)

        # High Card
        return (0, vals[:5])

    @staticmethod
    def compare(score1: tuple[int, list[int]], score2: tuple[int, list[int]]) -> int:
        """Compare two hand evaluation scores.

        Returns 1 if score1 > score2, -1 if score1 < score2, 0 if equal.
        """
        type1, kickers1 = score1
        type2, kickers2 = score2
        if type1 > type2:
            return 1
        if type1 < type2:
            return -1
        for k1, k2 in zip(kickers1, kickers2):
            if k1 > k2:
                return 1
            if k1 < k2:
                return -1
        return 0

    @staticmethod
    def hand_name(score: tuple[int, list[int]], lang: str = "en") -> str:
        """Return human-readable hand name in English or Traditional Chinese."""
        type_rank, kickers = score
        is_zh = lang.startswith("zh")
        rank_names = _RANK_NAMES_ZH if is_zh else _RANK_NAMES_EN

        k0 = kickers[0] if len(kickers) > 0 else 14
        k1 = kickers[1] if len(kickers) > 1 else 0

        if type_rank == 8:
            if k0 == 14:
                return "皇家同花順 (Royal Flush)" if is_zh else "Royal Flush"
            name = rank_names.get(k0, str(k0))
            return f"同花順 ({name} High Straight Flush)" if is_zh else f"Straight Flush, {name} High"
        if type_rank == 7:
            name = rank_names.get(k0, str(k0))
            return f"四條 {name} (Four of a Kind)" if is_zh else f"Four of a Kind, {name}s"
        if type_rank == 6:
            t_name = rank_names.get(k0, str(k0))
            p_name = rank_names.get(k1, str(k1))
            return f"葫蘆 ({t_name} 帶 {p_name})" if is_zh else f"Full House, {t_name}s full of {p_name}s"
        if type_rank == 5:
            name = rank_names.get(k0, str(k0))
            return f"同花 ({name} High Flush)" if is_zh else f"Flush, {name} High"
        if type_rank == 4:
            name = rank_names.get(k0, str(k0))
            return f"順子 ({name} High Straight)" if is_zh else f"Straight, {name} High"
        if type_rank == 3:
            name = rank_names.get(k0, str(k0))
            return f"三條 {name} (Three of a Kind)" if is_zh else f"Three of a Kind, {name}s"
        if type_rank == 2:
            p1 = rank_names.get(k0, str(k0))
            p2 = rank_names.get(k1, str(k1))
            return f"兩對 ({p1} 與 {p2})" if is_zh else f"Two Pair, {p1}s and {p2}s"
        if type_rank == 1:
            name = rank_names.get(k0, str(k0))
            return f"一對 {name} (One Pair)" if is_zh else f"One Pair of {name}s"
        name = rank_names.get(k0, str(k0))
        return f"高牌 {name} (High Card)" if is_zh else f"High Card, {name}"


class TableRules:
    """Manages seating order, blinds, action sequence, and legal moves."""

    @staticmethod
    def assign_positions(players: list[Player], button_seat: int) -> None:
        """Assign standard NLH position labels to active players."""
        active = [
            p
            for p in players
            if not p.sitting_out
            and p.finish_rank is None
            and (p.stack is None or p.stack > 0)
        ]
        if not active:
            return

        sorted_players = sorted(players, key=lambda p: p.seat_index if p.seat_index is not None else 0)
        n = len(sorted_players)

        # In 2-player heads-up: BTN is SB, other player is BB
        if len(active) == 2:
            btn_player = next(
                (p for p in active if p.seat_index == button_seat), active[0]
            )
            other = next(p for p in active if p.id != btn_player.id)
            btn_player.position = "BTN"
            other.position = "BB"
            return

        # 3+ players standard mapping
        pos_maps = {
            3: ["BTN", "SB", "BB"],
            4: ["BTN", "SB", "BB", "UTG"],
            5: ["BTN", "SB", "BB", "UTG", "CO"],
            6: ["BTN", "SB", "BB", "UTG", "MP", "CO"],
            7: ["BTN", "SB", "BB", "UTG", "MP", "HJ", "CO"],
            8: ["BTN", "SB", "BB", "UTG", "UTG+1", "MP", "HJ", "CO"],
            9: ["BTN", "SB", "BB", "UTG", "UTG+1", "MP", "MP+1", "HJ", "CO"],
            10: ["BTN", "SB", "BB", "UTG", "UTG+1", "UTG+2", "MP", "MP+1", "HJ", "CO"],
        }

        active_indices = [
            i for i, p in enumerate(sorted_players)
            if p in active
        ]
        btn_pos_in_active = 0
        for i, p_idx in enumerate(active_indices):
            if sorted_players[p_idx].seat_index == button_seat:
                btn_pos_in_active = i
                break

        ordered_active = (
            active_indices[btn_pos_in_active:] + active_indices[:btn_pos_in_active]
        )

        positions = pos_maps.get(len(ordered_active), ["BTN", "SB", "BB"] + [f"Seat {i+1}" for i in range(3, len(ordered_active))])
        for offset, p_idx in enumerate(ordered_active):
            player = sorted_players[p_idx]
            player.position = positions[offset] if offset < len(positions) else f"Seat {player.seat_index + 1 if player.seat_index is not None else 0}"

    @staticmethod
    def get_action_order(
        players: list[Player],
        button_seat: int,
        street: Street | str,
    ) -> list[int]:
        """Compute the clockwise acting order of player IDs for a given street."""
        active = [
            p for p in players
            if not p.folded and not p.all_in and not p.sitting_out and (p.stack is None or p.stack > 0)
        ]
        if not active:
            return []

        sorted_players = sorted(players, key=lambda p: p.seat_index if p.seat_index is not None else 0)
        n = len(sorted_players)
        street_str = street.value if isinstance(street, Street) else street

        # Heads-up:
        # Preflop: BTN (SB) acts first, then BB
        # Postflop: BB acts first, then BTN (SB)
        active_eligible = [p for p in sorted_players if not p.folded and not p.sitting_out]
        if len(active_eligible) == 2:
            btn = next((p for p in active_eligible if p.seat_index == button_seat), active_eligible[0])
            bb = next(p for p in active_eligible if p.id != btn.id)
            if street_str == "preflop":
                order = [btn, bb]
            else:
                order = [bb, btn]
            return [p.id for p in order if not p.folded and not p.all_in]

        # 3+ players:
        # Preflop starts after BB (UTG), which is 3 seats clockwise from BTN
        # Postflop starts with SB (first active left of BTN), which is 1 seat clockwise from BTN
        start_offset = 3 if street_str == "preflop" else 1

        ordered_ids: list[int] = []
        for offset in range(n):
            seat = (button_seat + start_offset + offset) % n
            player = next((p for p in sorted_players if p.seat_index == seat), None)
            if player and not player.folded and not player.all_in and not player.sitting_out:
                if player.id not in ordered_ids:
                    ordered_ids.append(player.id)
        return ordered_ids

    @staticmethod
    def calculate_legal_actions(
        player: Player,
        highest_bet: int,
        big_blind: int,
        last_raise_size: int,
        acted_players: set[int],
        pot: int,
    ) -> LegalActionSummary:
        """Calculate complete legal action vocabulary and raise presets."""
        stack = player.stack if player.stack is not None else 999999
        to_call = highest_bet - player.bet
        summary = LegalActionSummary()

        if player.folded or (player.stack is not None and player.stack <= 0) or player.all_in:
            summary.can_fold = False
            return summary

        # 1. Fold & Check & Call
        summary.can_fold = True
        if to_call <= 0:
            summary.can_check = True
            summary.can_call = False
            summary.call_amount = 0
        else:
            summary.can_check = False
            summary.can_call = True
            summary.call_amount = min(to_call, stack)

        # 2. All-in
        if stack > 0:
            summary.can_all_in = True
            summary.all_in_amount = stack

        # 3. Raise logic
        min_raise_increment = max(big_blind, last_raise_size)
        min_raise_to = highest_bet + min_raise_increment
        max_raise_to = player.bet + stack

        # Betting reopening check
        # A full raise clears ``acted_players`` in the state machine.  A short
        # all-in does not, so merely facing a higher total is not enough to
        # reopen raising for somebody who has already acted.
        can_raise_reopened = player.id not in acted_players

        if can_raise_reopened and max_raise_to >= min_raise_to and stack > to_call:
            summary.can_raise = True
            summary.min_raise_to = min_raise_to
            summary.max_raise_to = max_raise_to

            presets: list[RaisePreset] = []
            presets.append(RaisePreset(label="Min", target_total=min_raise_to, is_all_in=min_raise_to == max_raise_to))

            half_pot = highest_bet + max((pot + to_call) // 2, min_raise_increment)
            three_quarter_pot = highest_bet + max((pot + to_call) * 3 // 4, min_raise_increment)
            pot_size = highest_bet + max(pot + to_call, min_raise_increment)

            for label, val in [("½ Pot", half_pot), ("¾ Pot", three_quarter_pot), ("Pot", pot_size)]:
                if val < min_raise_to:
                    val = min_raise_to
                if val >= max_raise_to:
                    val = max_raise_to
                    presets.append(RaisePreset(label=label, target_total=val, is_all_in=True))
                else:
                    presets.append(RaisePreset(label=label, target_total=val, is_all_in=False))

            presets.append(RaisePreset(label="All-in", target_total=max_raise_to, is_all_in=True))
            seen_totals: set[int] = set()
            unique_presets: list[RaisePreset] = []
            for p in presets:
                if p.target_total not in seen_totals:
                    seen_totals.add(p.target_total)
                    unique_presets.append(p)
            summary.presets = unique_presets

        summary.can_time_bank = player.time_bank_available and not player.is_bot
        return summary
