Built adapters

These files follow the same adapter contract as your existing project:
- Recruitee: recruitee.py
- Personio: personio.py
- JazzHR: jazzhr.py
- Bullhorn: bullhorn.py
- Beamery: beamery.py
- Phenom: phenom.py
- ClearCompany: clearcompany.py
- ADP Recruiting Management: adp.py
- Infor Talent Acquisition: infor.py
- NeoGov: neogov.py

Implementation notes

- Recruitee, Personio, JazzHR and Bullhorn are the cleanest builds here because they target public feed or API-style patterns.
- Beamery is not really an ATS in the same way as the others, so this adapter is best treated as an optional career-site scraper.
- Phenom, ClearCompany, ADP, Infor and NeoGov are browser-first adapters that rely on JSON interception and Next.js payload extraction where available.
- For tenant-specific variants, you may need to tweak selectors, network hints or feed paths after testing against real board URLs.

Suggested next step

Wire these into your adapter registry and test one real board URL for each platform before batch rollout.
