// ==UserScript==
// @name         Linux.do & IDCFlare 回帖足迹
// @namespace    https://linux.do/
// @version      1.2.0
// @description  标记本人回复楼层并接入帖子详情时间轴跳转（同时适配 linux.do / idcflare.com）
// @author       dabao
// @match        https://linux.do/*
// @match        https://idcflare.com/*
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_addStyle
// @grant        GM_registerMenuCommand
// @run-at       document-idle
// @license      GPL-3.0
// @downloadURL  https://update.greasyfork.org/scripts/563725/Linuxdo%20%20IDCFlare%20%E5%9B%9E%E5%B8%96%E8%B6%B3%E8%BF%B9.user.js
// @updateURL    https://update.greasyfork.org/scripts/563725/Linuxdo%20%20IDCFlare%20%E5%9B%9E%E5%B8%96%E8%B6%B3%E8%BF%B9.meta.js
// ==/UserScript==

(function () {
    "use strict";

    const w = window;
    const d = document;
    const SID = location.host.replace(/\W/g, "");
    const DB = "disc-replied-db";
    const ST = "disc-replied-store";
    const K = { I: "disc_init", O: "disc_offset", T: "disc_time", C: "disc_count" };
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    const LIST_TAG_LIMIT = 6;
    const TIMELINE_MARKER_LIMIT = 24;
    const DETAIL_MENU_PAGE_SIZE = 40;

    // Discourse may use either a letter avatar or a real user avatar.
    const findUserName = () => {
        const currentUser = d.querySelector("#toggle-current-user, #current-user");
        if (!currentUser) return "";

        const cardName = currentUser.matches("[data-user-card]")
            ? currentUser.getAttribute("data-user-card")
            : currentUser.querySelector("[data-user-card]")?.getAttribute("data-user-card");
        if (cardName) return cardName.trim();

        const src = currentUser.querySelector("img.avatar")?.getAttribute("src") || "";
        const path = new URL(src, location.origin).pathname;

        return path.match(/\/letter_avatar\/([^/]+)\/\d+(?:\/|$)/)?.[1]
            || path.match(/\/letter_avatar_proxy\/(?:[^/]+\/)*([^/]+)\/\d+\.[^/]+$/)?.[1]
            || path.match(/\/user_avatar\/[^/]+\/([^/]+)\//)?.[1]
            || "";
    };

    function start(uName) {
        // Storage is isolated by site and username.
        const get = (key, def) => (GM_getValue(SID, {})[uName]?.[key] ?? def);
        const set = (key, value) => {
            const box = GM_getValue(SID, {});
            (box[uName] ||= {})[key] = value;
            GM_setValue(SID, box);
        };

    GM_addStyle(`
        .disc-replied-tags {
            display: flex;
            align-items: center;
            flex-wrap: wrap;
            gap: .35rem;
            margin-top: .35rem;
        }
        .disc-replied-tag.discourse-tag {
            position: relative;
            display: inline-flex !important;
            box-sizing: border-box;
            align-items: center;
            justify-content: center;
            min-width: 7.5rem;
            height: 1.65rem;
            min-height: 1.65rem;
            padding: .2rem .55rem !important;
            border: 1px solid #0f766e !important;
            border-radius: 4px !important;
            background-color: #0f766e !important;
            color: #fff !important;
            font-size: .86em;
            font-weight: 700 !important;
            line-height: 1.25;
            overflow: visible;
            vertical-align: middle;
            cursor: pointer;
            box-shadow: 0 1px 2px rgba(15, 118, 110, .25);
            transition:
                background-color .15s ease,
                border-color .15s ease,
                transform .15s ease;
        }
        .disc-replied-tag__label {
            display: block;
            min-width: 0;
            white-space: nowrap;
        }
        .disc-replied-tag__actions {
            position: absolute;
            top: calc(100% - .1rem);
            left: 50%;
            z-index: 30;
            display: flex;
            gap: 1px;
            box-sizing: border-box;
            width: 100%;
            height: 1.85rem;
            overflow: hidden;
            padding: .18rem .2rem .2rem;
            border: 1px solid #115e59;
            border-radius: .38rem;
            background: #115e59;
            box-shadow: 0 7px 15px rgba(15, 64, 61, .28);
            opacity: 0;
            pointer-events: none;
            transform: translate(-50%, -.35rem) scaleY(.9);
            transform-origin: top center;
            transition:
                opacity .16s ease,
                transform .22s cubic-bezier(.22, 1, .36, 1);
        }
        .disc-replied-action {
            display: grid;
            flex: 1 1 0;
            min-width: 0;
            place-items: center;
            color: rgba(255, 255, 255, .96);
            font-family: "Segoe UI Symbol", "Noto Sans Symbols 2", sans-serif;
            font-size: 1rem;
            font-weight: 800;
            line-height: 1;
            user-select: none;
            background-color: rgba(0, 0, 0, .1);
            transition:
                background-color .15s ease,
                transform .24s cubic-bezier(.22, 1, .36, 1);
        }
        .disc-replied-action:first-child {
            transform: translateX(-110%);
        }
        .disc-replied-action:last-child {
            transform: translateX(110%);
        }
        .disc-replied-action + .disc-replied-action {
            border-left: 1px solid rgba(255, 255, 255, .28);
        }
        .disc-replied-action:hover,
        .disc-replied-action:focus-visible {
            background-color: rgba(255, 255, 255, .16);
            outline: none;
        }
        .disc-replied-tag.discourse-tag:hover,
        .disc-replied-tag.discourse-tag:focus-visible {
            position: relative;
            z-index: 20;
            border-color: #115e59 !important;
            background-color: #115e59 !important;
            color: #fff !important;
            text-decoration: none;
            transform: translateY(-1px);
        }
        .disc-replied-tag.discourse-tag:hover .disc-replied-tag__actions,
        .disc-replied-tag.discourse-tag:focus-visible .disc-replied-tag__actions {
            opacity: 1;
            pointer-events: auto;
            transform: translate(-50%, 0) scaleY(1);
        }
        .disc-replied-tag.discourse-tag:hover .disc-replied-action:first-child,
        .disc-replied-tag.discourse-tag:focus-visible .disc-replied-action:first-child,
        .disc-replied-tag.discourse-tag:hover .disc-replied-action:last-child,
        .disc-replied-tag.discourse-tag:focus-visible .disc-replied-action:last-child {
            transform: translateX(0);
        }
        .disc-replied-tag.discourse-tag:focus-visible {
            outline: 2px solid #f59e0b;
            outline-offset: 2px;
        }
        .timeline-scrollarea-wrapper {
            position: relative;
        }
        .disc-replied-timeline-legend {
            position: absolute;
            top: .15rem;
            right: 0;
            z-index: 6;
            display: inline-flex;
            align-items: center;
            gap: .35rem;
            box-sizing: border-box;
            padding: .3rem .48rem;
            border: 1px solid rgba(107, 92, 231, .22);
            border-radius: .45rem;
            background: #eeeaff;
            color: #5143c9;
            font-size: .72rem;
            font-weight: 800;
            line-height: 1;
            white-space: nowrap;
            pointer-events: auto;
            cursor: pointer;
            user-select: none;
            appearance: none;
            transition:
                background-color .16s ease,
                border-color .16s ease,
                box-shadow .16s ease;
        }
        .disc-replied-timeline-legend:hover,
        .disc-replied-timeline-legend:focus-visible,
        .disc-replied-timeline-legend[aria-expanded="true"] {
            border-color: rgba(107, 92, 231, .44);
            background: #e4dfff;
            box-shadow: 0 2px 7px rgba(81, 67, 201, .16);
            outline: none;
        }
        .disc-replied-timeline-legend::before {
            content: "";
            width: .42rem;
            height: .42rem;
            border: 2px solid #fff;
            border-radius: 50%;
            background: #6b5ce7;
            box-shadow: 0 0 0 2px rgba(107, 92, 231, .18);
        }
        .disc-replied-timeline-legend::after {
            content: "⌄";
            margin-left: .08rem;
            color: #756bc8;
            font-size: .82rem;
            line-height: .7;
        }
        .disc-replied-timeline-menu {
            position: absolute;
            top: 2.15rem;
            right: 0;
            z-index: 10;
            display: grid;
            width: min(18rem, calc(100% - .5rem));
            max-height: 15rem;
            box-sizing: border-box;
            overflow-y: auto;
            padding: .38rem;
            border: 1px solid #d8d3ff;
            border-radius: .62rem;
            background: #fff;
            box-shadow: 0 12px 28px rgba(34, 30, 82, .2);
        }
        .disc-replied-timeline-menu[hidden] {
            display: none;
        }
        .disc-replied-timeline-menu__head {
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            gap: .7rem;
            padding: .42rem .5rem .5rem;
            border-bottom: 1px solid #eceaf8;
            color: #5143c9;
            font-size: .72rem;
            font-weight: 800;
        }
        .disc-replied-timeline-menu__head span {
            color: #8b879f;
            font-size: .66rem;
            font-weight: 600;
        }
        .disc-replied-timeline-menu__search {
            width: calc(100% - 1rem);
            min-height: 1.85rem;
            box-sizing: border-box;
            margin: .42rem .5rem .18rem;
            padding: .3rem .45rem;
            border: 1px solid #d8d3ff;
            border-radius: .4rem;
            color: #2d3340;
            background: #fbfaff;
            font: inherit;
            font-size: .72rem;
        }
        .disc-replied-timeline-menu__search:focus-visible {
            border-color: #8d83eb;
            outline: 2px solid rgba(107, 92, 231, .22);
            outline-offset: 1px;
        }
        .disc-replied-timeline-menu__results {
            display: grid;
            min-height: 1.8rem;
        }
        .disc-replied-timeline-menu__item {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: .7rem;
            min-width: 0;
            padding: .48rem .5rem;
            border-radius: .42rem;
            color: #2d3340;
            text-decoration: none !important;
            transition:
                background-color .14s ease,
                color .14s ease;
        }
        .disc-replied-timeline-menu__item:hover,
        .disc-replied-timeline-menu__item:focus-visible {
            background: #f0edff;
            color: #5143c9;
            outline: none;
        }
        .disc-replied-timeline-menu__floor,
        .disc-replied-timeline-menu__date {
            display: block;
            white-space: nowrap;
        }
        .disc-replied-timeline-menu__floor {
            overflow: hidden;
            font-size: .74rem;
            font-weight: 800;
            text-overflow: ellipsis;
        }
        .disc-replied-timeline-menu__date {
            flex: 0 0 auto;
            color: #8b879f;
            font-size: .68rem;
        }
        .disc-replied-timeline-menu__empty {
            padding: .8rem .5rem;
            color: #8b879f;
            font-size: .7rem;
            text-align: center;
        }
        .disc-replied-timeline-menu__footer {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: .35rem;
            margin-top: .18rem;
            padding: .42rem .5rem .08rem;
            border-top: 1px solid #eceaf8;
        }
        .disc-replied-timeline-menu__page {
            flex: 1 1 auto;
            color: #8b879f;
            font-size: .66rem;
            text-align: center;
            white-space: nowrap;
        }
        .disc-replied-timeline-menu__button {
            min-height: 1.6rem;
            padding: .18rem .38rem;
            border: 1px solid #d8d3ff;
            border-radius: .34rem;
            color: #5143c9;
            background: #fff;
            font: inherit;
            font-size: .66rem;
            font-weight: 700;
            cursor: pointer;
        }
        .disc-replied-timeline-menu__button:hover,
        .disc-replied-timeline-menu__button:focus-visible {
            border-color: #a49cf1;
            background: #f0edff;
            outline: none;
        }
        .disc-replied-timeline-menu__button:disabled {
            border-color: #ebe9f5;
            color: #c3bfd4;
            background: #fbfaff;
            cursor: default;
        }
        .disc-replied-timeline-layer {
            position: absolute;
            inset: 0;
            z-index: 8;
            pointer-events: none !important;
        }
        .disc-replied-timeline-marker {
            position: absolute;
            left: -16px;
            z-index: 9;
            display: grid;
            place-items: center;
            width: 28px;
            height: 28px;
            padding: 0;
            border: 0;
            border-radius: 50%;
            background: transparent;
            color: inherit;
            cursor: pointer;
            pointer-events: auto !important;
            text-decoration: none !important;
            transform: translateY(-50%);
        }
        .disc-replied-timeline-marker__dot {
            display: block;
            width: 9px;
            height: 9px;
            border: 2px solid #fff;
            border-radius: 50%;
            background: #6b5ce7;
            box-shadow:
                0 0 0 3px rgba(107, 92, 231, .18),
                0 1px 4px rgba(61, 49, 153, .35);
            transition:
                width .16s ease,
                height .16s ease,
                background-color .16s ease,
                box-shadow .16s ease;
        }
        .disc-replied-timeline-marker.is-cluster .disc-replied-timeline-marker__dot {
            width: 12px;
            height: 12px;
            background: #5143c9;
        }
        .disc-replied-timeline-marker__count {
            position: absolute;
            top: -4px;
            left: calc(50% + 4px);
            z-index: 2;
            display: grid;
            min-width: .78rem;
            height: .78rem;
            padding: 0 .12rem;
            place-items: center;
            border: 1px solid #fff;
            border-radius: 999px;
            color: #fff;
            background: #5143c9;
            box-shadow: 0 1px 3px rgba(61, 49, 153, .36);
            font-size: .55rem;
            font-weight: 800;
            line-height: 1;
        }
        .disc-replied-timeline-marker:hover .disc-replied-timeline-marker__dot,
        .disc-replied-timeline-marker:focus-visible .disc-replied-timeline-marker__dot,
        .disc-replied-timeline-marker.is-hovered .disc-replied-timeline-marker__dot,
        .disc-replied-timeline-marker.is-focused .disc-replied-timeline-marker__dot,
        .disc-replied-timeline-marker.is-current .disc-replied-timeline-marker__dot {
            width: 14px !important;
            height: 14px !important;
            background: #5143c9 !important;
            box-shadow:
                0 0 0 4px rgba(107, 92, 231, .22),
                0 2px 7px rgba(61, 49, 153, .38) !important;
        }
        .disc-replied-timeline-marker:focus-visible {
            outline: 2px solid #f59e0b;
            outline-offset: 2px;
        }
        .disc-replied-timeline-marker__popover {
            position: absolute;
            left: 25px;
            top: 50%;
            min-width: 8rem;
            box-sizing: border-box;
            padding: .48rem .6rem;
            border: 1px solid rgba(255, 255, 255, .12);
            border-radius: .5rem;
            background: #2e3440;
            color: #fff;
            box-shadow: 0 8px 20px rgba(30, 38, 49, .22);
            opacity: 0;
            pointer-events: none;
            transform: translateY(-50%) translateX(-4px);
            transition:
                opacity .16s ease,
                transform .16s ease;
            white-space: nowrap;
        }
        .disc-replied-timeline-marker__popover::before {
            position: absolute;
            top: 50%;
            left: -5px;
            width: 9px;
            height: 9px;
            background: #2e3440;
            content: "";
            transform: translateY(-50%) rotate(45deg);
        }
        .disc-replied-timeline-marker:hover .disc-replied-timeline-marker__popover,
        .disc-replied-timeline-marker:focus-visible .disc-replied-timeline-marker__popover,
        .disc-replied-timeline-marker.is-hovered .disc-replied-timeline-marker__popover,
        .disc-replied-timeline-marker.is-focused .disc-replied-timeline-marker__popover,
        .disc-replied-timeline-marker.is-current .disc-replied-timeline-marker__popover {
            opacity: 1 !important;
            transform: translateY(-50%) translateX(0) !important;
        }
        .disc-replied-timeline-marker__post,
        .disc-replied-timeline-marker__date {
            position: relative;
            z-index: 1;
            display: block;
        }
        .disc-replied-timeline-marker__post {
            font-size: .75rem;
            font-weight: 800;
            letter-spacing: .01em;
        }
        .disc-replied-timeline-marker__date {
            margin-top: .18rem;
            color: #cbd3da;
            font-size: .68rem;
        }
        .timeline-scrollarea-wrapper .timeline-last-read {
            z-index: 5;
        }
        @media (prefers-reduced-motion: reduce) {
            .disc-replied-tag.discourse-tag,
            .disc-replied-tag__label,
            .disc-replied-tag__actions,
            .disc-replied-timeline-legend,
            .disc-replied-timeline-marker,
            .disc-replied-timeline-marker__dot,
            .disc-replied-timeline-marker__popover,
            .disc-replied-timeline-menu__item {
                transition: none;
            }
        }
    `);

    const dbAct = (mode, fn) => new Promise((resolve, reject) => {
        const request = indexedDB.open(DB, 1);

        request.onerror = () => reject(request.error || new Error("DB Open Failed"));
        request.onupgradeneeded = (event) => {
            const db = event.target.result;
            if (db.objectStoreNames.contains(ST)) db.deleteObjectStore(ST);

            const store = db.createObjectStore(ST, {
                keyPath: ["uid", "topic_id", "post_number"]
            });
            store.createIndex("utopic", ["uid", "topic_id"]);
        };
        request.onsuccess = () => {
            const tx = request.result.transaction([ST], mode);
            tx.onerror = () => reject(tx.error || new Error("Transaction Error"));
            tx.onabort = () => reject(tx.error || new Error("Transaction Aborted"));

            try {
                fn(tx.objectStore(ST), resolve, reject);
            } catch (error) {
                reject(error);
            }
        };
    });

    const fetchActions = (offset, limit) =>
        fetch(
            `/user_actions.json?offset=${offset}&limit=${limit}&username=${encodeURIComponent(uName)}&filter=5`,
            { credentials: "same-origin" }
        ).then((response) => (
            response.ok
                ? response.json()
                : Promise.reject(new Error(`HTTP ${response.status}`))
        ));

    async function sync(mode) {
        const isInit = mode === "init";
        const limit = 30;
        let offset = isInit ? get(K.O, 0) : 0;
        let added = 0;

        while (true) {
            const data = await fetchActions(offset, limit);
            const actions = data?.user_actions || [];
            if (!actions.length) break;
            let changedOnPage = 0;

            for (const action of actions) {
                const topicId = Number(action?.topic_id) || 0;
                const postNumber = Number(action?.post_number) || 0;
                if (!topicId || !postNumber) continue;

                const createdAt = action?.created_at
                    || action?.createdAt
                    || action?.timestamp
                    || "";
                const existing = await dbAct("readonly", (store, resolve) => {
                    store.get([uName, topicId, postNumber]).onsuccess = (event) => {
                        resolve(event.target.result || null);
                    };
                });

                if (!existing) {
                    await dbAct("readwrite", (store, resolve) => {
                        store.put({
                            uid: uName,
                            topic_id: topicId,
                            post_number: postNumber,
                            created_at: createdAt
                        }).onsuccess = () => resolve();
                    });
                    added++;
                    changedOnPage++;
                } else if (createdAt && !existing.created_at) {
                    await dbAct("readwrite", (store, resolve) => {
                        store.put({
                            ...existing,
                            created_at: createdAt
                        }).onsuccess = () => resolve();
                    });
                    changedOnPage++;
                }
            }

            if (isInit) set(K.O, offset);
            offset += actions.length;

            // Keep scanning until a complete page is already cached. This repairs
            // holes left by interrupted or older incremental synchronizations and
            // backfills dates for records written by older script versions.
            if (!isInit && !changedOnPage) break;

            if (!isInit && offset >= 300) break;
            await sleep(800);
        }

        const total = await dbAct("readonly", (store, resolve) => {
            const index = store.index("utopic");
            index.count(
                IDBKeyRange.bound([uName, 0], [uName, Infinity])
            ).onsuccess = (event) => resolve(event.target.result);
        });

        set(K.C, total);
        set(K.T, Date.now());

        if (isInit) {
            set(K.I, true);
            set(K.O, 0);
        }

        return added;
    }

    // Keep custom tags outside Discourse's native ul.discourse-tags.
    function ensureTagsBox(tr) {
        const main = tr.querySelector("td.main-link");
        if (!main) return null;

        let box = main.querySelector("div.disc-replied-tags");
        if (box) return box;

        const bottom = main.querySelector(".link-bottom-line");
        if (!bottom) return null;

        box = d.createElement("div");
        box.className = "discourse-tags disc-replied-tags";
        box.setAttribute("role", "list");
        box.setAttribute("aria-label", "已回复");
        bottom.appendChild(box);
        return box;
    }

    const getReplyRecords = (topicId) => dbAct("readonly", (store, resolve) => {
        const index = store.index("utopic");
        const range = IDBKeyRange.bound([uName, topicId], [uName, topicId]);
        const records = [];

        index.openCursor(range).onsuccess = (event) => {
            const cursor = event.target.result;
            if (cursor) {
                const postNumber = Number(cursor.value?.post_number) || 0;
                if (postNumber > 0) records.push({ ...cursor.value, post_number: postNumber });
                cursor.continue();
            } else {
                resolve(
                    records
                        .sort((a, b) => a.post_number - b.post_number)
                        .filter((record, index, all) => (
                            index === 0
                            || record.post_number !== all[index - 1].post_number
                        ))
                );
            }
        };
    });

    const getPostNumbers = async (topicId) => (
        (await getReplyRecords(topicId)).map((record) => record.post_number)
    );

    const buildPostHref = (baseHref, topicId, postNumber) => {
        const url = new URL(baseHref, location.origin);
        const pathname = url.pathname.replace(/\/+$/, "");

        if (pathname.endsWith(`/${topicId}`)) {
            url.pathname = `${pathname}/${postNumber}`;
        } else if (/\/\d+$/.test(pathname)) {
            url.pathname = pathname.replace(/\/\d+$/, `/${postNumber}`);
        } else {
            url.pathname = `${pathname}/${postNumber}`;
        }
        url.hash = "";
        return url.href;
    };

    const replyDateCache = new Map();
    let detailRoot = null;
    let detailObs = null;
    let detailResizeObs = null;
    let detailRenderFrame = 0;
    let detailRenderSerial = 0;
    let detailMenuDismissBound = false;

    const getTopicIdFromHref = (href = location.href) => {
        const pathname = new URL(href, location.origin).pathname;
        return Number(pathname.match(/\/t\/[^/]+\/(\d+)/)?.[1]) || 0;
    };

    const getPostNumberFromHref = (href = location.href) => {
        const pathname = new URL(href, location.origin).pathname;
        return Number(
            pathname.match(/\/t\/[^/]+\/\d+\/(\d+)(?:\/|$)/)?.[1]
                || pathname.match(/\/t\/\d+\/(\d+)(?:\/|$)/)?.[1]
                || 1
        );
    };

    const formatTimelineDate = (value) => {
        if (value === undefined || value === null || value === "") return "";

        const raw = String(value);
        const date = /^\d+$/.test(raw)
            ? new Date(Number(raw))
            : new Date(value);

        if (Number.isNaN(date.getTime())) return "";

        return (date.getMonth() + 1) + "月 " + date.getDate() + "日";
    };

    const getLoadedPostDate = (postNumber) => {
        const post = d.getElementById("post_" + postNumber);
        const dateNode = post?.querySelector(".relative-date, time, [data-time]");
        if (!dateNode) return "";

        return formatTimelineDate(dateNode.getAttribute("data-time"))
            || dateNode.getAttribute("title")
            || dateNode.getAttribute("datetime")
            || dateNode.textContent.trim();
    };

    const getReplyDateText = (record) => (
        formatTimelineDate(
            record?.created_at
                || record?.createdAt
                || record?.timestamp
                || record?.post_created_at
        )
        || getLoadedPostDate(record?.post_number)
    );

    const getTimelineTotal = (root, records) => {
        const text = root.querySelector(".timeline-replies")?.textContent || "";
        const matched = text.replace(/,/g, "").match(/\d+\s*\/\s*(\d+)/);
        return Number(matched?.[1])
            || Math.max(1, ...records.map((record) => Number(record.post_number) || 1));
    };

    const groupTimelineRecords = (records, total) => {
        if (records.length <= TIMELINE_MARKER_LIMIT) {
            return records.map((record) => [record]);
        }

        const denominator = Math.max(1, total - 1);
        const groups = new Map();
        records.forEach((record) => {
            const postNumber = Number(record.post_number) || 1;
            const ratio = Math.min(1, Math.max(0, (postNumber - 1) / denominator));
            const slot = Math.min(
                TIMELINE_MARKER_LIMIT - 1,
                Math.floor(ratio * TIMELINE_MARKER_LIMIT)
            );
            const group = groups.get(slot) || [];
            group.push(record);
            groups.set(slot, group);
        });

        return [...groups.entries()]
            .sort(([left], [right]) => left - right)
            .map(([, group]) => group);
    };

    const positionDetailMarkers = (layer, total) => {
        const scrollArea = layer.parentElement;
        if (!scrollArea) return;

        const height = scrollArea.clientHeight
            || scrollArea.getBoundingClientRect().height
            || 300;
        const handleHeight = scrollArea.querySelector(".timeline-scroller")
            ?.getBoundingClientRect().height
            || 50;
        const halfHandle = Math.min(height / 2, handleHeight / 2);
        const travel = Math.max(0, height - (halfHandle * 2));
        const denominator = Math.max(1, total - 1);

        layer.querySelectorAll(".disc-replied-timeline-marker").forEach((marker) => {
            const postNumber = Number(marker.dataset.postNumber) || 1;
            const ratio = Math.min(1, Math.max(0, (postNumber - 1) / denominator));
            marker.style.top = (halfHandle + (ratio * travel)) + "px";
        });
    };

    const fetchReplyDate = async (topicId, postNumber) => {
        const loadedDate = getLoadedPostDate(postNumber);
        if (loadedDate) return loadedDate;

        const key = String(topicId) + ":" + String(postNumber);
        if (!replyDateCache.has(key)) {
            replyDateCache.set(key, (async () => {
                try {
                    const jsonUrl = new URL(
                        buildPostHref("/t/topic/" + topicId, topicId, postNumber),
                        location.origin
                    );
                    jsonUrl.pathname += ".json";

                    const response = await fetch(jsonUrl.href, {
                        credentials: "same-origin"
                    });
                    if (!response.ok) return "";

                    const payload = await response.json();
                    return formatTimelineDate(
                        payload?.created_at
                            || payload?.post?.created_at
                            || payload?.post?.post?.created_at
                    );
                } catch (error) {
                    console.debug("[detail reply date]", error);
                    return "";
                }
            })());
        }

        return replyDateCache.get(key);
    };

    const hydrateDetailMarkerDate = async (marker, topicId, postNumber) => {
        const dateNode = marker.querySelector(".disc-replied-timeline-marker__date");
        if (!dateNode || dateNode.dataset.resolved === "true") return;

        dateNode.textContent = "日期读取中…";
        const dateText = await fetchReplyDate(topicId, postNumber);
        if (!marker.isConnected) return;

        dateNode.textContent = dateText || "日期暂不可用";
        dateNode.dataset.resolved = "true";
        marker.title = dateText
            ? "本人回复第 " + postNumber + " 楼，" + dateText + "；点击跳转"
            : "本人回复第 " + postNumber + " 楼；点击跳转";
        marker.setAttribute(
            "aria-label",
            dateText
                ? "本人回复第 " + postNumber + " 楼，" + dateText + "，点击跳转"
                : "本人回复第 " + postNumber + " 楼，点击跳转"
        );
    };

    const closeDetailMenu = () => {
        const root = detailRoot;
        const menu = root?.querySelector(".disc-replied-timeline-menu");
        const legend = root?.querySelector(".disc-replied-timeline-legend");
        if (!menu || !legend) return;

        menu.hidden = true;
        legend.setAttribute("aria-expanded", "false");
    };

    const bindDetailMenuDismiss = () => {
        if (detailMenuDismissBound) return;

        d.addEventListener("pointerdown", (event) => {
            if (detailRoot && !detailRoot.contains(event.target)) closeDetailMenu();
        });
        d.addEventListener("keydown", (event) => {
            if (event.key === "Escape") closeDetailMenu();
        });
        detailMenuDismissBound = true;
    };

    const createDetailMarker = (record, topicId, currentPostNumber) => {
        const postNumber = Number(record.post_number) || 0;
        const dateText = getReplyDateText(record) || "日期待加载";
        const marker = d.createElement("a");
        const dot = d.createElement("span");
        const popover = d.createElement("span");
        const postLabel = d.createElement("span");
        const dateLabel = d.createElement("span");

        marker.className = "disc-replied-timeline-marker";
        marker.href = buildPostHref(location.href, topicId, postNumber);
        marker.title = "本人回复第 " + postNumber + " 楼，"
            + dateText + "；点击跳转";
        marker.setAttribute(
            "aria-label",
            "本人回复第 " + postNumber + " 楼，"
                + dateText + "，点击跳转"
        );
        marker.dataset.postNumber = String(postNumber);
        if (postNumber === currentPostNumber) marker.classList.add("is-current");

        dot.className = "disc-replied-timeline-marker__dot";
        popover.className = "disc-replied-timeline-marker__popover";
        postLabel.className = "disc-replied-timeline-marker__post";
        dateLabel.className = "disc-replied-timeline-marker__date";
        postLabel.textContent = "我的回复 · #" + postNumber;
        dateLabel.textContent = dateText;
        popover.append(postLabel, dateLabel);
        marker.append(dot, popover);

        const showMarker = () => {
            marker.classList.add("is-hovered");
            hydrateDetailMarkerDate(marker, topicId, postNumber);
        };
        const hideMarker = () => {
            marker.classList.remove("is-hovered");
        };
        marker.addEventListener("pointerenter", showMarker);
        marker.addEventListener("mouseenter", showMarker);
        marker.addEventListener("pointerleave", hideMarker);
        marker.addEventListener("mouseleave", hideMarker);
        marker.addEventListener("focus", () => {
            marker.classList.add("is-focused");
            hydrateDetailMarkerDate(marker, topicId, postNumber);
        });
        marker.addEventListener("blur", () => {
            marker.classList.remove("is-focused");
        });
        return marker;
    };

    const createDetailClusterMarker = (
        records,
        topicId,
        currentPostNumber,
        onOpen
    ) => {
        const firstPostNumber = Number(records[0]?.post_number) || 1;
        const lastPostNumber = Number(records[records.length - 1]?.post_number)
            || firstPostNumber;
        const positionPostNumber = Math.round(
            (firstPostNumber + lastPostNumber) / 2
        );
        const marker = d.createElement("button");
        const dot = d.createElement("span");
        const count = d.createElement("span");
        const popover = d.createElement("span");
        const postLabel = d.createElement("span");
        const dateLabel = d.createElement("span");
        const hasCurrent = records.some(
            (record) => Number(record.post_number) === currentPostNumber
        );
        const rangeLabel = firstPostNumber === lastPostNumber
            ? "#" + firstPostNumber
            : "#" + firstPostNumber + "–#" + lastPostNumber;

        marker.className = "disc-replied-timeline-marker is-cluster";
        marker.type = "button";
        marker.dataset.postNumber = String(positionPostNumber);
        marker.title = "本人回复 " + rangeLabel + "，共 "
            + records.length + " 条；点击查看该段楼层";
        marker.setAttribute(
            "aria-label",
            "本人回复 " + rangeLabel + "，共 "
                + records.length + " 条，点击查看该段楼层"
        );
        if (hasCurrent) marker.classList.add("is-current");

        dot.className = "disc-replied-timeline-marker__dot";
        count.className = "disc-replied-timeline-marker__count";
        count.textContent = records.length > 99 ? "99+" : String(records.length);
        count.setAttribute("aria-hidden", "true");
        popover.className = "disc-replied-timeline-marker__popover";
        postLabel.className = "disc-replied-timeline-marker__post";
        dateLabel.className = "disc-replied-timeline-marker__date";
        postLabel.textContent = "我的回复 · " + rangeLabel;
        dateLabel.textContent = records.length + " 条 · 点击查看楼层";
        popover.append(postLabel, dateLabel);
        marker.append(dot, count, popover);

        const open = () => onOpen(records, rangeLabel);
        marker.addEventListener("click", open);
        marker.addEventListener("pointerenter", () => {
            marker.classList.add("is-hovered");
        });
        marker.addEventListener("mouseenter", () => {
            marker.classList.add("is-hovered");
        });
        marker.addEventListener("pointerleave", () => {
            marker.classList.remove("is-hovered");
        });
        marker.addEventListener("mouseleave", () => {
            marker.classList.remove("is-hovered");
        });
        marker.addEventListener("focus", () => {
            marker.classList.add("is-focused");
        });
        marker.addEventListener("blur", () => {
            marker.classList.remove("is-focused");
        });
        return marker;
    };

    const createDetailMenuItem = (record, topicId) => {
        const postNumber = Number(record.post_number) || 0;
        let dateText = getReplyDateText(record) || "日期待加载";
        const item = d.createElement("a");
        const floorLabel = d.createElement("span");
        const dateLabel = d.createElement("span");

        item.className = "disc-replied-timeline-menu__item";
        item.href = buildPostHref(location.href, topicId, postNumber);
        item.setAttribute("role", "menuitem");
        item.setAttribute(
            "aria-label",
            "跳转到本人回复第 " + postNumber + " 楼，" + dateText
        );
        item.title = "跳转到本人回复第 " + postNumber + " 楼";

        floorLabel.className = "disc-replied-timeline-menu__floor";
        dateLabel.className = "disc-replied-timeline-menu__date";
        floorLabel.textContent = "本人回复 · #" + postNumber;
        dateLabel.textContent = dateText;
        item.append(floorLabel, dateLabel);

        item.addEventListener("pointerenter", async () => {
            if (dateText !== "日期待加载") return;
            const resolved = await fetchReplyDate(topicId, postNumber);
            if (!item.isConnected || !resolved) return;
            dateText = resolved;
            dateLabel.textContent = resolved;
            item.setAttribute(
                "aria-label",
                "跳转到本人回复第 " + postNumber + " 楼，" + resolved
            );
        });
        return item;
    };

    const createDetailMenuController = (topicId, records) => {
        const legend = d.createElement("button");
        const menu = d.createElement("div");
        const menuHead = d.createElement("div");
        const menuTitle = d.createElement("strong");
        const menuCount = d.createElement("span");
        const search = d.createElement("input");
        const results = d.createElement("div");
        const footer = d.createElement("div");
        const previous = d.createElement("button");
        const pageLabel = d.createElement("span");
        const next = d.createElement("button");
        const reset = d.createElement("button");
        let scopeRecords = records;
        let scopeLabel = "我的回复楼层";
        let page = 0;

        const render = () => {
            const query = search.value.trim().replace(/^#/, "");
            const filtered = query
                ? scopeRecords.filter((record) => (
                    String(record.post_number).includes(query)
                ))
                : scopeRecords;
            const pageCount = Math.max(
                1,
                Math.ceil(filtered.length / DETAIL_MENU_PAGE_SIZE)
            );

            page = Math.min(page, pageCount - 1);
            const start = page * DETAIL_MENU_PAGE_SIZE;
            const visible = filtered.slice(start, start + DETAIL_MENU_PAGE_SIZE);
            results.replaceChildren();

            if (!visible.length) {
                const empty = d.createElement("div");
                empty.className = "disc-replied-timeline-menu__empty";
                empty.textContent = "没有匹配的楼层";
                results.appendChild(empty);
            } else {
                visible.forEach((record) => {
                    results.appendChild(createDetailMenuItem(record, topicId));
                });
            }

            menuTitle.textContent = scopeLabel;
            menuCount.textContent = query
                ? `${filtered.length} / ${scopeRecords.length} 个`
                : `${scopeRecords.length} 个`;
            pageLabel.textContent = filtered.length
                ? `${start + 1}–${Math.min(
                    start + DETAIL_MENU_PAGE_SIZE,
                    filtered.length
                )} / ${filtered.length}`
                : "0 个结果";
            previous.disabled = page <= 0;
            next.disabled = page >= pageCount - 1;
            reset.hidden = scopeRecords === records;
        };

        legend.className = "disc-replied-timeline-legend";
        legend.type = "button";
        legend.setAttribute("role", "button");
        legend.setAttribute("aria-haspopup", "dialog");
        legend.setAttribute("aria-expanded", "false");
        legend.textContent = "我的回复 " + records.length;
        legend.setAttribute(
            "aria-label",
            "本人在本帖回复过 " + records.length + " 个楼层"
        );

        menu.className = "disc-replied-timeline-menu";
        menu.id = "disc-replied-timeline-menu-" + topicId;
        menu.hidden = true;
        menu.setAttribute("role", "dialog");
        menu.setAttribute("aria-label", "本人回复楼层清单");
        legend.setAttribute("aria-controls", menu.id);
        legend.title = "展开本人回复楼层清单，可搜索并按页跳转";

        menuHead.className = "disc-replied-timeline-menu__head";
        menuTitle.textContent = scopeLabel;
        menuCount.textContent = records.length + " 个";
        menuHead.append(menuTitle, menuCount);

        search.className = "disc-replied-timeline-menu__search";
        search.type = "search";
        search.placeholder = "搜索楼层号，例如 128";
        search.setAttribute("aria-label", "搜索本人回复楼层号");
        search.addEventListener("input", () => {
            page = 0;
            render();
        });

        results.className = "disc-replied-timeline-menu__results";

        footer.className = "disc-replied-timeline-menu__footer";
        previous.className = "disc-replied-timeline-menu__button";
        previous.type = "button";
        previous.textContent = "上一页";
        next.className = "disc-replied-timeline-menu__button";
        next.type = "button";
        next.textContent = "下一页";
        pageLabel.className = "disc-replied-timeline-menu__page";
        reset.className = "disc-replied-timeline-menu__button";
        reset.type = "button";
        reset.textContent = "全部";
        reset.title = "查看本帖全部回复楼层";
        previous.addEventListener("click", () => {
            if (previous.disabled) return;
            page--;
            render();
        });
        next.addEventListener("click", () => {
            if (next.disabled) return;
            page++;
            render();
        });
        reset.addEventListener("click", () => open(records, "我的回复楼层"));
        footer.append(previous, pageLabel, next, reset);
        menu.append(menuHead, search, results, footer);

        const open = (nextRecords = records, nextLabel = "我的回复楼层") => {
            scopeRecords = nextRecords;
            scopeLabel = nextLabel;
            search.value = "";
            page = 0;
            render();
            menu.hidden = false;
            legend.setAttribute("aria-expanded", "true");
            search.focus({ preventScroll: true });
        };

        legend.addEventListener("click", (event) => {
            event.stopPropagation();
            if (menu.hidden) open();
            else closeDetailMenu();
        });

        render();
        return { legend, menu, open };
    };

    const renderDetailTimeline = async (root) => {
        if (!root) return;

        const serial = ++detailRenderSerial;
        const topicId = getTopicIdFromHref();
        const scrollArea = root.querySelector(".timeline-scrollarea");
        if (!topicId || !scrollArea) return;

        const records = await getReplyRecords(topicId);
        if (
            serial !== detailRenderSerial
            || root !== d.querySelector(".timeline-scrollarea-wrapper")
        ) {
            return;
        }

        scrollArea.querySelector(".disc-replied-timeline-layer")?.remove();
        root.querySelector(".disc-replied-timeline-legend")?.remove();
        root.querySelector(".disc-replied-timeline-menu")?.remove();
        if (!records.length) return;

        const total = getTimelineTotal(root, records);
        const currentPostNumber = getPostNumberFromHref();
        const layer = d.createElement("div");
        const menuController = createDetailMenuController(topicId, records);
        const { legend, menu } = menuController;
        const markerGroups = groupTimelineRecords(records, total);

        layer.className = "disc-replied-timeline-layer";
        layer.dataset.total = String(total);
        layer.dataset.markerCount = String(markerGroups.length);
        const markerHint = markerGroups.length < records.length
            ? `时间线已聚合为 ${markerGroups.length} 个位置；点击带数字紫点查看该段楼层`
            : "点击紫色点跳转到本人回复楼层";
        legend.title = markerHint + "；点击右上角清单可搜索全部楼层";

        markerGroups.forEach((group) => {
            if (group.length === 1) {
                layer.appendChild(
                    createDetailMarker(group[0], topicId, currentPostNumber)
                );
                return;
            }

            layer.appendChild(
                createDetailClusterMarker(
                    group,
                    topicId,
                    currentPostNumber,
                    (segment, rangeLabel) => menuController.open(
                        segment,
                        "回复区间 " + rangeLabel
                    )
                )
            );
        });

        scrollArea.appendChild(layer);
        root.appendChild(legend);
        root.appendChild(menu);
        bindDetailMenuDismiss();
        positionDetailMarkers(layer, total);
    };

    const resizeDetailMarkers = () => {
        const layer = detailRoot?.querySelector(".disc-replied-timeline-layer");
        if (!layer) return;
        positionDetailMarkers(layer, Number(layer.dataset.total) || 1);
    };

    const queueDetailRender = () => {
        if (!detailRoot || detailRenderFrame) return;

        detailRenderFrame = w.requestAnimationFrame(() => {
            detailRenderFrame = 0;
            renderDetailTimeline(detailRoot);
        });
    };

    const detailOwnSelector = [
        ".disc-replied-timeline-layer",
        ".disc-replied-timeline-legend",
        ".disc-replied-timeline-menu"
    ].join(", ");

    const isOwnDetailNode = (node) => {
        const element = node?.nodeType === Node.ELEMENT_NODE
            ? node
            : node?.parentElement || node?.parentNode;
        return !!element?.closest?.(detailOwnSelector);
    };

    const isRelevantDetailMutation = (mutation) => {
        if (isOwnDetailNode(mutation.target)) return false;

        if (mutation.type === "attributes") {
            return true;
        }

        const nodes = [
            ...mutation.addedNodes,
            ...mutation.removedNodes
        ];
        return !nodes.length || nodes.some((node) => !isOwnDetailNode(node));
    };

    const detachDetail = () => {
        detailRenderSerial++;
        detailObs?.disconnect();
        detailResizeObs?.disconnect();
        detailObs = null;
        detailResizeObs = null;

        if (detailRenderFrame) {
            w.cancelAnimationFrame(detailRenderFrame);
            detailRenderFrame = 0;
        }

        detailRoot?.querySelector(".disc-replied-timeline-layer")?.remove();
        detailRoot?.querySelector(".disc-replied-timeline-legend")?.remove();
        detailRoot?.querySelector(".disc-replied-timeline-menu")?.remove();
        detailRoot = null;
    };

    const attachDetail = () => {
        const root = d.querySelector(".timeline-scrollarea-wrapper");
        if (!root) return false;

        if (detailRoot === root && detailObs) {
            queueDetailRender();
            return true;
        }

        detachDetail();
        detailRoot = root;
        detailObs = new MutationObserver((mutations) => {
            if (mutations.some(isRelevantDetailMutation)) queueDetailRender();
        });
        detailObs.observe(root, {
            childList: true,
            subtree: true
        });

        const scrollArea = root.querySelector(".timeline-scrollarea");
        if (w.ResizeObserver && scrollArea) {
            detailResizeObs = new w.ResizeObserver(resizeDetailMarkers);
            detailResizeObs.observe(scrollArea);
        }

        queueDetailRender();
        return true;
    };

    const createReplyListTag = (baseHref, topicId, postNumber, options = {}) => {
        const count = Number(options.count) || 1;
        const firstPostNumber = Number(options.firstPostNumber) || postNumber;
        const lastPostNumber = Number(options.lastPostNumber) || postNumber;
        const isSummary = count > 1;
        const tag = d.createElement("a");
        const rangeLabel = firstPostNumber === lastPostNumber
            ? "第 " + postNumber + " 楼"
            : "第 " + firstPostNumber + "–" + lastPostNumber + " 楼";
        const targetLabel = isSummary
            ? "最近回复第 " + postNumber + " 楼"
            : "第 " + postNumber + " 楼";

        tag.className = "discourse-tag box disc-replied-tag";
        tag.innerHTML = `
            <span class="disc-replied-tag__label">${isSummary
                ? `已回复 ${count} 条`
                : `已回复 #${postNumber}`}</span>
            <span class="disc-replied-tag__actions" aria-hidden="true">
                <span class="disc-replied-action" title="进入当前页">↪</span>
                <span class="disc-replied-action" title="打开新标签页">↗</span>
            </span>`;
        tag.title = isSummary
            ? `本帖已回复 ${count} 条（${rangeLabel}）。左侧进入${targetLabel}，右侧打开新标签页；详情页右上角“我的回复 ${count}”可搜索任意楼层`
            : `左侧进入当前页，右侧打开新标签页：${rangeLabel}`;
        tag.setAttribute(
            "aria-label",
            isSummary
                ? `本帖已回复 ${count} 条，范围 ${rangeLabel}；左侧进入${targetLabel}，右侧打开新标签页`
                : `已回复${rangeLabel}；左侧进入当前页，右侧打开新标签页`
        );
        tag.dataset.postNumber = String(postNumber);
        tag.dataset.replyCount = String(count);
        tag.href = buildPostHref(baseHref, topicId, postNumber);
        tag.addEventListener("click", (event) => {
            if (event.button !== 0) return;

            const rect = tag.getBoundingClientRect();
            const midpoint = rect.left + (rect.width / 2);
            const isRightSide = event.detail > 0
                && Number.isFinite(event.clientX)
                && event.clientX > midpoint;

            if (isRightSide) {
                event.preventDefault();
                w.open(tag.href, "_blank", "noopener,noreferrer");
            }
        });
        return tag;
    };

    async function mark() {
        if (!d.querySelector("table.topic-list")) return;

        const rows = [...d.querySelectorAll("tr.topic-list-item[data-topic-id]")];
        if (!rows.length) return;

        await Promise.all(rows.map(async (tr) => {
            const topicId = Number(tr.getAttribute("data-topic-id")) || 0;
            if (!topicId) return;

            // Remove tags from both the current format and the previous ul format.
            tr.querySelectorAll(".disc-replied-tag").forEach((node) => {
                const item = node.closest("li");
                const ownBox = node.closest(".disc-replied-tags");

                if (item && !ownBox) item.remove();
                else node.remove();
            });

            const postNumbers = await getPostNumbers(topicId);
            if (!postNumbers.length) return;

            const tagsBox = ensureTagsBox(tr);
            if (!tagsBox) return;
            // A second mark() may finish while the IndexedDB read is pending.
            // Clear again here so overlapping renders stay idempotent.
            tagsBox.querySelectorAll(".disc-replied-tag").forEach((node) => node.remove());

            const topicA = tr.querySelector('a.raw-topic-link[href^="/t/"]');
            const baseHref = topicA?.getAttribute("href") || `/t/${topicId}/1`;

            const isSummary = postNumbers.length > LIST_TAG_LIMIT;
            if (isSummary) {
                tagsBox.appendChild(
                    createReplyListTag(
                        baseHref,
                        topicId,
                        postNumbers[postNumbers.length - 1],
                        {
                            count: postNumbers.length,
                            firstPostNumber: postNumbers[0],
                            lastPostNumber: postNumbers[postNumbers.length - 1]
                        }
                    )
                );
                return;
            }

            postNumbers.forEach((postNumber) => {
                tagsBox.appendChild(
                    createReplyListTag(baseHref, topicId, postNumber)
                );
            });
        }));
    }

    let listObs = null;
    let waitObs = null;
    let lastHref = location.href;

    const attachList = () => {
        const body = d.querySelector("tbody.topic-list-body");
        if (!body) return false;

        listObs?.disconnect();
        listObs = new MutationObserver((mutations) => {
            if (mutations.some((mutation) => (
                mutation.addedNodes.length || mutation.removedNodes.length
            ))) {
                mark();
            }
        });
        listObs.observe(body, { childList: true });
        return true;
    };

    const attach = () => {
        const listAttached = attachList();
        const detailAttached = attachDetail();
        return listAttached || detailAttached;
    };

    const clearWaitObserver = () => {
        waitObs?.disconnect();
        waitObs = null;
    };

    const onRoute = () => {
        let attempts = 0;
        const maxAttempts = 20;

        listObs?.disconnect();
        listObs = null;
        detachDetail();
        clearWaitObserver();

        const check = setInterval(() => {
            if (attach()) {
                clearInterval(check);
                mark();
                queueDetailRender();

                navigator.locks?.request?.(
                    `disc_sync_${uName}`,
                    { ifAvailable: true },
                    async (lock) => {
                        if (!lock || !get(K.I)) return;

                        try {
                            await sync("inc");
                            mark();
                            queueDetailRender();
                        } catch (error) {
                            console.warn("[sync inc]", error);
                        }
                    }
                );
            } else {
                attempts++;
                if (attempts >= maxAttempts) clearInterval(check);
            }
        }, 100);
    };

    const hook = () => {
        const pushState = history.pushState;
        const replaceState = history.replaceState;

        history.pushState = function () {
            const result = pushState.apply(this, arguments);
            if (location.href !== lastHref) {
                lastHref = location.href;
                onRoute();
            }
            return result;
        };

        history.replaceState = function () {
            const result = replaceState.apply(this, arguments);
            if (location.href !== lastHref) {
                lastHref = location.href;
                onRoute();
            }
            return result;
        };

        addEventListener("popstate", () => {
            if (location.href !== lastHref) {
                lastHref = location.href;
                onRoute();
            }
        });
    };

    (async () => {
        hook();

        if (!attach()) {
            const waitTarget = d.body;

            if (waitTarget) {
                waitObs = new MutationObserver(() => {
                    if (attach()) {
                        clearWaitObserver();
                        mark();
                    }
                });
                waitObs.observe(waitTarget, {
                    childList: true,
                    subtree: true
                });
            }
        }

        mark();
        queueDetailRender();

        try {
            if (!get(K.I)) {
                const last = get(K.O, 0);
                const ok = w.confirm(
                    `${last > 0 ? "断点续传" : "初始化回复数据"}\n\n` +
                    `${last > 0
                        ? `检测到账号 [${uName}] 上次同步中断，offset=${last}。\n是否继续？`
                        : `检测到账号 [${uName}] 尚未同步记录。\n是否开始抓取？`}`
                );

                if (!ok) return;

                const added = await sync("init");
                w.alert(`同步完成：新增 ${added} 条记录`);
                mark();
                queueDetailRender();
            } else {
                await sync("inc");
                mark();
                queueDetailRender();
            }
        } catch (error) {
            console.error("[Discourse Replied Tag] Critical Error", error);
        }
    })();

    GM_registerMenuCommand(" 重置回复数据", async () => {
        if (!w.confirm("确认重置？仅清空当前账号的缓存记录。")) return;

        try {
            await dbAct("readwrite", (store, resolve) => {
                const index = store.index("utopic");
                const range = IDBKeyRange.bound([uName, 0], [uName, Infinity]);

                index.openCursor(range).onsuccess = (event) => {
                    const cursor = event.target.result;
                    if (cursor) {
                        cursor.delete();
                        cursor.continue();
                    } else {
                        resolve();
                    }
                };
            });

            const box = GM_getValue(SID, {});
            delete box[uName];
            GM_setValue(SID, box);
            location.reload();
        } catch (error) {
            w.alert("重置失败: " + (error?.message || error));
        }
    });

    GM_registerMenuCommand(" 数据统计信息", () => {
        const timeStr = get(K.T)
            ? new Date(get(K.T)).toLocaleString()
            : "无";

        w.alert(
            `用户: ${uName}\n` +
            `状态: ${get(K.I) ? "✅ 完成" : "⏳ 未初始化"}\n` +
            `更新: ${timeStr}\n` +
            `记录: ${get(K.C, 0)} 条`
        );
    });
    }

    let bootObs = null;
    let started = false;

    const boot = () => {
        if (started) return true;

        const uName = findUserName();
        if (!uName) return false;

        started = true;
        bootObs?.disconnect();
        bootObs = null;
        start(uName);
        return true;
    };

    if (!boot() && d.documentElement) {
        bootObs = new MutationObserver(() => boot());
        bootObs.observe(d.documentElement, {
            childList: true,
            subtree: true,
            attributes: true,
            attributeFilter: ["src", "data-user-card"]
        });
    }
})();
