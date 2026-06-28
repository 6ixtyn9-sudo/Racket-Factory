"""Supabase client for edges and edge_picks.
Uses service_role key only. Credentials loaded via load_dotenv().
"""
from supabase import create_client
from dotenv import load_dotenv
import os


def get_client():
    """Return authenticated Supabase client (service role)."""
    load_dotenv()
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise ValueError("Supabase credentials not found in environment")
    return create_client(url, key)


def upsert_edges(client, edges_list):
    """Upsert list of edge dicts into the edges table."""
    if not edges_list:
        return
    resp = client.table("edges").upsert(edges_list, on_conflict="sport_id,source_id,name").execute()
    print(f"Upserted 'edges': {len(edges_list)} rows")
    return resp


def delete_picks_for_date(client, picked_for: str):
    """Delete existing edge_picks rows for a target date before authoritative re-sync."""
    resp = (
        client.table("edge_picks")
        .delete()
        .eq("picked_for", picked_for)
        .execute()
    )
    print(f"Deleted existing 'edge_picks' for {picked_for}")
    return resp


def upsert_picks(client, picks_list):
    """Upsert list of pick dicts into edge_picks (ignore conflicts)."""
    if not picks_list:
        return
    resp = (
        client.table("edge_picks")
        .upsert(picks_list, on_conflict="edge_id,event_id,market,selection")
        .execute()
    )
    print(f"Upserted 'edge_picks': {len(picks_list)} rows")
    return resp
