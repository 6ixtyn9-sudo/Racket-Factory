"""WhatsApp Business professional notification engine."""
from __future__ import annotations
import urllib.parse
import urllib.request
import logging

logger = logging.getLogger(__name__)

def format_whatsapp_message(date_str: str, px_preds: list, bc_preds: list, fb_preds: list) -> str:
    lines = [f"🎾 *RACKET FACTORY PREDICTIONS* 🎾\n📅 Date: {date_str}\n"]
    
    if px_preds:
        lines.append("🤖 *PredixSport AI*")
        for p in px_preds:
            lines.append(f"• *{p['player_home']}* vs *{p['player_away']}*")
            lines.append(f"  └ 🏆 Pick: {p['predicted_winner_name']} ({max(p['prob_home'], p['prob_away'])}%)")
        lines.append("")
        
    if bc_preds:
        lines.append("📊 *BetClan AI*")
        for p in bc_preds:
            time_str = f" [KO: {p['match_time']}]" if p.get('match_time') else ""
            lines.append(f"• *{p['player_home']}* vs *{p['player_away']}*{time_str}")
            lines.append(f"  └ 🏆 Pick: {p['predicted_winner_name']} ({max(p['prob_home'], p['prob_away'])}%)")
        lines.append("")

    if fb_preds:
        lines.append("🔮 *Forebet AI*")
        for p in fb_preds:
            p1, p2 = p.get('player_home', '?'), p.get('player_away', '?')
            prob = p.get('prob_home') if str(p.get('predicted_winner')) == "1" else p.get('prob_away')
            lines.append(f"• *{p1}* vs *{p2}*")
            lines.append(f"  └ 🏆 Pick: Player {p.get('predicted_winner')} ({prob}%)")
        lines.append("")
        
    if not px_preds and not bc_preds and not fb_preds:
        lines.append("ℹ️ *No predictions found for today.*")
        
    lines.append("\n⚠️ Flat stakes only. Bet responsibly.")
    return "\n".join(lines)

def send_callmebot_whatsapp(apikey: str, phone: str, message_text: str) -> str:
    clean_phone = "".join(filter(str.isdigit, str(phone)))
    encoded_text = urllib.parse.quote(message_text)
    # Using the correct .php endpoint based on the Edge-Factory documentation
    url = f"https://api.callmebot.com/whatsapp.php?phone={clean_phone}&text={encoded_text}&apikey={apikey}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            return resp.read().decode("utf-8", "replace")
    except Exception as e:
        logger.error(f"Failed to send WhatsApp message: {e}")
        return ""