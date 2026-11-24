# Notifications

The lollo pages can notify you on your phone or computer: the vault when an agent asks for a secret, when it locks or when an erase is scheduled, and the browser when an agent needs you for a hand-off or a checkout. You turn notifications on once per device, on the lollo's Notifications page, and from then on that device gets every lollo notification. Tapping one opens its page; you answer there, never from the notification. The Notifications page is on whenever the vault (`lollo-vault`) or the browser (`lollo-browser`) is; it has no profile of its own. Lettuce's own notifications are separate and turned on in Lettuce.

![A sender hands a notification to the notifications service, which pushes it to every device you enrolled; a tap opens a lollo page](diagrams/notifications-flow.svg)

An interactive version, [notifications-flow.html](diagrams/notifications-flow.html), opens in a browser from a clone of this repo.

How the notifications service is built and wired, for developers: [notifications/README.md](../notifications/README.md).

## Using it
1. **On iPhone or iPad, add Lollo to the Home Screen first.** iOS delivers notifications only to web apps on the Home Screen, from iOS 16.4. Open any lollo page except the code editor (its pages are code-server's own), `https://<your lollo>/` say (`<your lollo>` is your stack's lollo address, `<name>-lollo.<domain>`), press Share, then **Add to Home Screen**, and open **Lollo** from the Home Screen. Every one of them gives the same app, which opens on the lollo's home page; the vault and the browser open inside it. On a computer or an Android phone, skip this step.
2. **Open Notifications:** `https://<your lollo>/notifications/`. The lollo's home page links to it as **Notifications**, and so do the vault's and the browser's pages.
3. **Press Enable on this device** and allow notifications when the browser asks. The page then says "Notifications are on for this device." Do this once on each device you want notified.
4. **Press Send a test.** Every device of yours with notifications on shows "Lollo: Test notification", and the page says how many devices it reached. Only your own devices get it.
5. **Check Your devices.** The page lists each device of yours with notifications on: when it was added and the push service it uses (Apple, Google or Mozilla, for example). The one you are on is marked **This device**. Other people's devices are never shown.
6. **To stop notifications on a device**, open the page on that device and press **Disable on this device**. A device you did not turn off but is missing from the list gets no notifications (its browser dropped them, someone turned them off, its push service stopped answering, or the stack was restored with another push key): open the page on it and press **Enable on this device** again.

What each page notifies. Every title starts with the name of the page that sent it, "Vault: ", "Browser: " or "Lollo: " (a test), put there by the notifications service, so a page can't send a notification that reads as another's.

| From | When | Tapping it opens |
|---|---|---|
| Vault | an agent asks for a secret (it names the secret and the fields, never a value); the vault locked and an agent is waiting; an erase was scheduled, is an hour away after a restart, was cancelled or was done | the request's page, or the vault |
| Browser | an agent needs a hand-off (a CAPTCHA, a code, a sign-in, a payment), a checkout waits for you, or a checkout went ahead on a site you allowed | the hand-off's page, or the task's history |
| Notifications | you pressed **Send a test** | this page |

Notifications have no buttons: approving a request, placing an order or declining happens only on the page the notification opens. A tap opens that page in a window already on it, or on the lollo's home page or Notifications page; otherwise in a new window, so a tap never replaces another lollo page you are working in.

### After upgrading
Releases before the Notifications page had a switch in the vault and another in the browser, each with its own Home Screen app on iPhone. Those stopped when you upgraded: nothing tells you, and until you enable notifications again you get none (the vault's requests still post their link in the chat). On each device, open `https://<your lollo>/notifications/` and press **Enable on this device**. On iPhone, add **Lollo** to the Home Screen (step 1) and delete the old **Vault** and **Browser** apps.

## Running it
- **Turning it on:** with `lollo-vault` or `lollo-browser` in the profiles, `lolloctl add` and `lolloctl profiles` route `https://<lollo>/notifications/` to the `lollo-notifications` container, and `lolloctl up` starts it; with neither, it does not run and the address is not routed. Changing the profiles does not restart it while one of them stays on.
- **Upgrading a stack made before it:** `lolloctl up` makes no Cloudflare call, so it does not add the route. With a sender on, it warns once that the tunnel does not route `https://<lollo>/notifications/` yet. Run `lolloctl profiles <list>` once with the current list (the one `lolloctl profiles` shows, `cloudflared,lollo-vault` say): it routes every stack and, with the list unchanged, restarts none. Until then the Notifications page gives a 404.
- **Keys:** `lolloctl up` gives each stack, once, the push key pair (`LOLLO_VAPID_PUBLIC_KEY`, `LOLLO_VAPID_PRIVATE_KEY`) and a token for each sender (`NOTIFY_TOKEN_VAULT`, `NOTIFY_TOKEN_BROWSER`), in `stack.env`, whatever the profiles, so a page turned on later can notify at once. **Never change or delete the key pair:** every device's subscription is bound to it. The service keeps the public key with the subscriptions, and when it starts with another one it deletes every subscription and logs how many: each device shows as off and gets nothing until it is enabled again.
- **Data:** the devices' subscriptions live in the Docker volume `lollo-<name>_notifications-data`, not under `stacks/<name>/`. `lolloctl backup` includes it and `lolloctl restore` puts it back, so a restored stack still reaches its devices (see [operations.md](operations.md#backups)). Restored next to a `stack.env` with another key pair (an older one, say), the subscriptions are deleted at start, and each device enables notifications again. It holds at most 50 devices per stack; past that the oldest is dropped.
- **People removed:** someone removed from a stack with `lolloctl users` gets no further notification, even on devices they enrolled.
- **Isolation:** the container has a read-only root filesystem and no capabilities, runs as a non-root user, is on the stack's own network only and has no host mounts. Its volume is mounted by nothing else. `lolloctl check` refuses a stack where any of that is not true, or where a sender's token is in any other container.
- **Logs:** `lolloctl logs <name> lollo-notifications` shows which page sent what kind of notification (the sender and a tag such as `vault-request`), how many devices it reached, failures by push service, and devices removed. Titles and bodies are never logged.

## Security model
What the notifications service protects, and what it does not.

- **Only the vault and the browser can send, and each only its own.** Each holds a token that only it and the notifications service have; agents can't read either, and a request from a browser page or through the internet is refused. A browser notification can't link to the vault or look like a vault request, and the other way round; a link outside the lollo pages is refused. Each sender may send at most 30 notifications a minute and 300 an hour, so a page stuck in a loop can't ring your phone all night, and one page reaching its limit doesn't stop the other's.
- **Each notification names its sender.** The service puts "Vault: " or "Browser: " in front of every title, whatever the page sent, so the browser (or a summary an agent wrote) can't show a notification that reads as a vault request.
- **One sender never delays another.** Each page's notifications wait in a queue of their own and go to all your devices at once, so a busy browser or a slow device doesn't hold up or refuse the vault's. A device whose push service times out or can't be reached 3 times in a row is removed from **Your devices**.
- **A tap only opens a lollo page.** A notification opens the vault, the browser or the Notifications page, never another site, and has no buttons that act for you.
- **No secret value in a notification.** The vault's name a secret and its fields, never a value; the browser removes values from what it sends. Notifications are encrypted from the service to your device, but they show on your lock screen: anyone holding your phone can read them.
- **Nothing is stored on your device.** The lollo's service worker caches nothing, so every page still needs your Cloudflare Access sign-in.
- **Any script on the lollo's address can turn a device's notifications off, but it can't send any.** The lollo's pages share one address, and so does the code editor: a script running there, a code editor extension's view say, can unsubscribe the device it runs on. It can't add a device or send a test without your sign-in and the page's form token, and it can't send you a notification, since a push service takes only notifications signed with your stack's key. If notifications stop on a device, check **Your devices**: a device missing from the list no longer gets them, the vault's erase warnings included.
- **The service is a single point:** when it is down, no lollo notification is sent, and the vault refuses to schedule an erase until it is back. It restarts on its own.

## Limits
- iOS delivers notifications only to web apps added to the Home Screen, and only on iOS 16.4 or later.
- At most 50 devices per stack; adding one more drops the oldest.
- A notification the service accepted is lost if it stops before sending it, and a push service keeps one for a device that is off for 10 minutes.
- A device whose push service times out or can't be reached 3 times in a row is removed; enable it again from the Notifications page.
- **Send a test** works at most 5 times a minute.
