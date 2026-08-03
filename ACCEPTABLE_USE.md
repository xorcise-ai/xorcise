# Acceptable Use Policy

**Last updated: 2 August 2026**

The canonical version of this policy is published at <https://xorcise.ai/policies/acceptable-use>.
This copy travels with the source so that anyone who obtains XORCISE from this repository, or
from the `xorcise` distribution, has it in front of them.

## 1. About this policy

1.1 XORCISE.AI operated by Fifth Domain Pty Ltd ACN 606 251 585 ("XORCISE.AI", "we", "us" or
"our") publishes XORCISE: an engine for evaluating cyber AI agents, a library of deliberately
vulnerable missions, and the tooling around them.

1.2 XORCISE is offensive-security tooling. It stands up exploitable targets, it drives an
autonomous agent against them, and it records what the agent did. That is what makes it useful
for measuring cyber AI, and it is also what makes it capable of harm if it is pointed somewhere
it does not belong.

1.3 This policy states what we consider acceptable use. It applies to the software, the mission
library, the playbooks skill, our website and anything else we publish.

1.4 XORCISE is licensed under the Apache License 2.0. That licence grants you broad rights and
we are not trying to narrow them here. **This policy is not an additional licence condition and
it does not restrict what Apache-2.0 permits.** It states the terms on which we will help you,
support you, and let you use our name, and it states plainly what we will not assist with.

## 2. The one rule that matters

2.1 **Only point XORCISE at systems you own, or that you have specific written authorisation to
test.**

2.2 Everything else in this policy follows from that sentence. A mission runs inside a contained
environment on your own host, and the per-run network fence is built to keep the agent inside
it. But XORCISE does not and cannot verify that a target you supply, a mission you author, or a
network you attach is yours. That judgement is yours, and so is the liability for getting it
wrong.

## 3. Acceptable use

3.1 Evaluating a cyber AI agent, yours or a third party's, against the published mission library
on infrastructure you control.

3.2 Authoring your own missions that model your own systems, and evaluating agents against them.

3.3 Running XORCISE as part of an authorised security assessment, red-team engagement or
penetration test where you hold written authorisation covering the scope.

3.4 Research, teaching, training and capability assessment, including publishing results that
reflect badly on a model, a vendor, or on us.

3.5 Building products, services and internal tooling on top of XORCISE, commercial or otherwise,
subject to Apache-2.0 and to our Trademark Policy
(<https://xorcise.ai/policies/trademark>).

## 4. Unacceptable use

4.1 Directing XORCISE, or an agent under evaluation, at any system, network, account or dataset
you do not own and are not specifically authorised in writing to test.

4.2 Using XORCISE to develop, refine, benchmark or validate capability intended for unauthorised
intrusion, extortion, ransomware, destructive operations, or the targeting of critical
infrastructure, safety systems or medical systems.

4.3 Using XORCISE to build or improve tooling whose purpose is to evade detection, defeat
security controls, or conceal an intrusion, other than within an authorised assessment of those
controls.

4.4 Using XORCISE to target individuals: surveillance, stalking, harassment, doxxing, or the
collection of personal information without a lawful basis.

4.5 Extracting the mission library, the rubrics or the run corpus to train or fine-tune a model
whose purpose falls within 4.2, 4.3 or 4.4.

4.6 Removing, disabling or circumventing the network fence, the run isolation or the evidence
recording in order to run a mission against something outside its contained environment.

4.7 Representing a result as produced by XORCISE when it was produced by a modified engine, a
modified rubric or a modified mission, contrary to the Trademark Policy.

4.8 Any use that breaks the law where you are, where your target is, or where your
infrastructure sits.

## 5. Export control and sanctions

5.1 XORCISE is developed and published in Australia. Australian export controls, including the
Defence Trade Controls Act 2012 (Cth) and the Defence and Strategic Goods List, apply to certain
intrusion software and related technology. Depending on where you are and what you do with it,
the export control regimes of other jurisdictions may apply too.

5.2 We publish XORCISE as open-source software in the public domain sense contemplated by those
regimes, and we do not provide it under any arrangement that would constitute a controlled
supply, service or transfer of technology by us.

5.3 **You are responsible for your own compliance.** If you download, redistribute, deploy,
fork, or provide services built on XORCISE, you are responsible for determining whether any
export control, sanctions or trade restriction applies to what you are doing, and for complying
with it. That includes any obligation arising when you move it across a border or make it
available to a person in another jurisdiction.

5.4 We will not knowingly supply XORCISE, support or commercial services to any person or entity
subject to Australian, United Nations, United States, United Kingdom or European Union
sanctions, or located in a jurisdiction subject to comprehensive sanctions.

5.5 If your organisation requires a specific export-control classification or an end-use
statement before it can adopt XORCISE, write to legal@xorcise.ai and tell us what you need. We
would rather answer that question directly than have you guess.

## 6. Vulnerable-by-design software

6.1 The mission library deliberately contains software with known, exploitable vulnerabilities,
including a host running Apache 2.4.49 carrying CVE-2021-41773 and others like it. This is
intentional and it is the point.

6.2 Mission images are not signed and carry no software bill of materials. A default local
install binds an unauthenticated REST API, console and OTLP ingest to loopback, plus the
Docker bridge gateway on native Linux, so any local process — and any container on that
bridge — can reach them. The bind widens to the IPv4 wildcard only if you set
`XORCISE_HOST=0.0.0.0` explicitly, or on Linux when the bridge gateway cannot be determined
at boot.

6.3 Run XORCISE on a host you are willing to treat as untrusted, on a network segment with no
route into anything you care about. Do not run it on a workstation holding credentials, on a
corporate network, or on shared infrastructure. [SECURITY.md](SECURITY.md) and
<https://xorcise.ai/security> set out every port as shipped and everything we have not hardened
yet.

## 7. Reporting misuse

7.1 If you become aware of XORCISE being used contrary to this policy, tell us at
contact@xorcise.ai.

7.2 To report a security flaw in XORCISE itself, use security@xorcise.ai and the process in
[SECURITY.md](SECURITY.md).

## 8. What we do about a breach

8.1 XORCISE is open-source software that runs on your machine. We have no telemetry, no licence
server and no kill switch, so we cannot technically prevent misuse, and we are not going to
pretend otherwise by writing enforcement powers into this policy that do not exist.

8.2 What we can do, and will: decline support, decline commercial engagement, withdraw
permission to use our name and marks, remove access to any service we do operate, and report
conduct to the relevant authorities where the law requires it or the seriousness warrants it.

## 9. Changes

9.1 We may update this policy. Material changes will be reflected in the date at the top of this
page, and the version history is in the repository that publishes it.

9.2 Questions about this policy go to legal@xorcise.ai.
