"""Synthetic loopback servers for browser tests; no live job state or ATS calls."""

import asyncio
import sys
import time
from types import SimpleNamespace

import uvicorn
from jobpilot.core import server as core
from jobpilot.core.profile_store import UserProfile
from jobpilot.gigs import server as gigs

profile = UserProfile(first_name='Test', last_name='Applicant', email='test@invalid.test')
core.get_profile_store = lambda: SimpleNamespace(load=lambda: profile)
core.reconcile_queue_with_tracker = lambda: (0, 0)
core.load_queue = lambda: []
core.get_application_tracker = lambda: SimpleNamespace(get_recent=lambda **kwargs: [], get_stats=lambda: {}, get_status_counts=lambda: {})
card = {'id':'demo', 'company':'Sample Company', 'role':'Engineer', 'score':95, 'pay':'$150,000', 'location':'Remote', 'offer':'Engineering', 'is_mailto':False, 'apply_target':f'http://127.0.0.1:{sys.argv[1]}/', 'resume':'resume.pdf', 'crib':{}}
gigs.swipe.session_meta = lambda: {'criteria_pills':['Remote','Score ≥60'], 'resumes':{}, 'on_demand':True}
gigs.swipe.build_queue = lambda **kwargs: [SimpleNamespace(id='demo')]
gigs.swipe.card = lambda gig: card
def slow_decision(*args):
    time.sleep(6)
    return 'sent'

gigs.swipe.record_decision = slow_decision
gigs.swipe.undo_decision = lambda *args: None

async def main():
    await asyncio.gather(*[uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, access_log=False)).serve() for app, port in [(core.app,int(sys.argv[1])),(gigs.app,int(sys.argv[2]))]])
if __name__ == "__main__":
    asyncio.run(main())
