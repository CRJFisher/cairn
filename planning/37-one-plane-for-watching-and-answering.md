# 37 — One plane for watching and answering

**North star ([principles 2 and 3](../PRINCIPLES.md)).** The page where a person keeps tabs on
sessions is also where they answer the ones that are asking. One place to look, one place to act.

**Status: downstream idea**, after [36](36-keeping-tabs.md) and [35](35-human-in-the-loop-steps.md)
each work alone.

## Why

Dagu's UI shows steps and logs, and can complete its own human tasks, but it cannot show a
session's meaning or a question raised mid-session. 36 already builds a store and a page; 35 needs
somewhere for a question to appear and an answer to be written. They are the same store, read and
written in two directions: summaries and questions out, answers in.

## To investigate

- **Write path.** A static page cannot write; answering needs a local server or a CLI fallback
  (`cairn answer <run> <step> …`). Which is the minimum?
- **Routing an answer.** To a blocked `ask_human` call, to a Dagu human task (`dagu human-task
complete`), or to the next follow-on run — one answer shape for all three.
- **Notification.** How a waiting question reaches a person who is not looking (push, email,
  Dagu's `handler_on.wait`).
- **Who may answer.** Only a person; an agent answering defeats the purpose (35, question 6).
