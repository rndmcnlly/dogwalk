---
name: invite-someone
description: the caller wants to invite someone (a friend, a colleague) to Dogwalk, asks about invitations they sent, wants to add or fix a friend's number on one, cancel one, hear the words again, or asks who invited them.
---

An Invitation lets the caller bring someone else onto Dogwalk by voice alone: three Invite Code words that register one new phone within 24 hours. The caller may have at most three active at once. You operate it yourself with the invitation tools; sessions never see it.

Often the friend is right there listening, and the caller may not know the friend's number yet. Either is fine:

1. Ask whether they want to tie the invitation to the friend's number. Unbound, whoever says the words first from an unregistered phone gets in, so the words should stay between the two of them. Bound, only that number can use them.
2. To bind, take the number as the caller (or the friend, on speaker) says it. Call create_invitation (or set_invitation_recipient for an existing one) with caller_confirmed_readback false: it returns the number's spoken digit groups. Read them back group by group ("plus one, eight three one, five five five, zero one two three: is that right?"). If anything is wrong, take the correction and read it back again. Only after a clear yes, call again with true. Ten digits are taken as a US number only for a caller calling from the US; otherwise ask for the country code. Confirming a number on this call does not show the friend owns it; their own call does that.
3. Say the three words slowly, one at a time, with a short pause between them, and offer to repeat them. Repeat them as often as asked. Suggest the friend write them down or say them back.
4. Tell them how the friend redeems it: call this same Dogwalk number from their own phone (the bound one, if bound), say the three words when asked, and say yes when the words are read back. That call registers the phone and hangs up; their next call starts their own workspace and a short tour. The words work once, for 24 hours.

Later calls: list_invitations tells you each invitation's status, its recipient, and once redeemed, when and by which number (and whether that was the number it was bound to). The caller may add or correct a number while an invitation is still active, following the same readback. revoke_invitation removes one from their list: an active one stops working; a redeemed one only leaves the list, and the friend stays registered. When the caller asks who invited them, inspect_registration answers with the inviter's number, or says an operator's Invite Code admitted them.

Numbers are the caller's own relationships: speak them only to this caller, when asked or while confirming, and never put them or the words in a prompt to a session.

## Voice Frontend guidance

When the Session Orchestrator gives you invite words or a phone number to say, say them plainly and slowly, never in the theme's words: each invite word on its own, a phone number in the digit groups given, with a short pause between groups. Repeat them exactly whenever asked. Treat "yes", "that's right", or a correction after a readback as the caller's answer and think it over.
