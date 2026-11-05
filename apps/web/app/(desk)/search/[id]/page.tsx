"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/Button";
import { EvidenceTag } from "@/components/EvidenceTag";
import { FormError } from "@/components/Field";
import { Loading } from "@/components/Progress";
import { BandLabel, Stamp } from "@/components/Stamp";
import { messageOf, post } from "@/lib/api";
import { count, date, rank, stamp } from "@/lib/format";
import { useKeys } from "@/lib/keys";
import type { FaceBox, MatchImage, MatchPerson, Results } from "@/lib/types";
import { useResource } from "@/lib/useResource";
import styles from "./page.module.css";

const LEAD_NOTICE = "Investigative lead only. Verify manually before any action.";
const FLOOR_STEP = 0.01;
// A band marker, not a measurement. Officers get the band and the rank, never a percentage.
const BAND_WIDTH: Record<MatchPerson["band"], string> = { STRONG: "100%", POSSIBLE: "66%", WEAK: "33%" };

function qualityWarning(face: FaceBox | undefined) {
  if (face?.quality.too_blurry) return "This photo is blurry, so expect weaker matches.";
  if (face?.quality.too_small) return "This face is small, so expect weaker matches.";
  if (face?.quality.too_turned) return "This face is turned away, so expect weaker matches.";
  return null;
}

function imageCaption(image: MatchImage, total: number) {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `IMG ${pad(image.position)}/${pad(total)} · ENROLLED ${date(image.enrolled_at)} · SRC ${image.source}`;
}

function ProbeBox({ results, face }: { results: Results; face: FaceBox }) {
  const [x1, y1, x2, y2] = face.box;
  return (
    <span
      className={styles.probeBox}
      style={{
        left: `${(x1 / results.width) * 100}%`,
        top: `${(y1 / results.height) * 100}%`,
        width: `${((x2 - x1) / results.width) * 100}%`,
        height: `${((y2 - y1) / results.height) * 100}%`,
      }}
    />
  );
}

function ProbePhoto({ results, face }: { results: Results; face: FaceBox | undefined }) {
  if (results.purged) {
    return (
      <div className={styles.purged}>
        <Stamp word="PURGED" />
      </div>
    );
  }
  return (
    <EvidenceTag
      src={`/api/search/${results.id}/probe`}
      alt={`Probe photo for case ${results.case_number}`}
      overlay={face && results.faces.length > 1 ? <ProbeBox results={results} face={face} /> : null}
      caption={`PROBE · CASE ${results.case_number}`}
    />
  );
}

interface RowProps {
  person: MatchPerson;
  results: Results;
  face: FaceBox | undefined;
  selected: boolean;
  onSelect: () => void;
}

function MatchRow({ person, results, face, selected, onSelect }: RowProps) {
  const ref = useRef<HTMLLIElement>(null);
  const [image, setImage] = useState<MatchImage | undefined>(person.images[0]);
  const best = person.images[0];
  const fileHref = `/records/${person.person_id}?search=${results.id}`;

  useEffect(() => {
    if (selected) ref.current?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  return (
    <li ref={ref} className={selected ? `${styles.row} ${styles.selected}` : styles.row}>
      <button type="button" className={styles.summary} aria-expanded={selected} onClick={onSelect}>
        <span className={styles.rank}>{rank(person.rank)}</span>
        {best ? (
          <img src={`/api/records/images/${best.image_id}?size=128`} alt="" className={styles.avatar} />
        ) : null}
        <span className={styles.who}>
          <span className={styles.record}>{person.record_id}</span>
          <span className={styles.matchCount}>
            {person.images_matched} of {person.images_total} images match
          </span>
        </span>
        <BandLabel band={person.band} />
        <span className={styles.score}>
          <span className={styles.track} aria-hidden="true">
            <span
              className={`${styles.fill} ${styles[person.band.toLowerCase()]}`}
              style={{ width: BAND_WIDTH[person.band] }}
            />
          </span>
        </span>
      </button>
      {selected ? (
        <div className={styles.detail}>
          <div className={styles.compare}>
            <ProbePhoto results={results} face={face} />
            {image ? (
              <EvidenceTag
                src={`/api/records/images/${image.image_id}?size=800`}
                alt={`Gallery image ${image.position} of record ${person.record_id}`}
                caption={`${imageCaption(image, person.images_total)} · ${image.band}`}
              />
            ) : null}
          </div>
          <div className={styles.detailSide}>
            <p className="label">Matching images</p>
            <ul className={styles.thumbs}>
              {person.images.map((item) => {
                const open = image?.image_id === item.image_id;
                return (
                  <li key={item.image_id}>
                    <button
                      type="button"
                      className={open ? `${styles.thumb} ${styles.thumbOpen}` : styles.thumb}
                      aria-pressed={open}
                      aria-label={`Compare image ${item.position}, ${item.band}`}
                      onClick={() => setImage(item)}
                    >
                      <img src={`/api/records/images/${item.image_id}?size=128`} alt="" width={64} height={64} />
                      <span className={styles.thumbScore}>{item.band}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
            <Link href={fileHref} className={styles.open}>
              <Button as="span" variant="solid">
                Open file
              </Button>
            </Link>
          </div>
        </div>
      ) : null}
    </li>
  );
}

function Sheet({ results, reload }: { results: Results; reload: () => Promise<void> }) {
  const router = useRouter();
  const [offset, setOffset] = useState(0);
  const [showWeak, setShowWeak] = useState(false);
  const [cursor, setCursor] = useState(0);
  const [attachError, setAttachError] = useState<string | null>(null);

  const floor = (results.floor ?? 0) + offset;
  const visible = useMemo(
    () => results.people.filter((p) => p.confidence >= floor),
    [results, floor],
  );
  const leads = visible.filter((p) => p.band !== "WEAK");
  const weak = visible.filter((p) => p.band === "WEAK");
  const listed = showWeak ? [...leads, ...weak] : leads;
  const selected = listed[Math.min(cursor, listed.length - 1)];

  useKeys({
    j: () => setCursor((c) => Math.min(c + 1, Math.max(listed.length - 1, 0))),
    k: () => setCursor((c) => Math.max(c - 1, 0)),
    Enter: () => {
      if (selected) router.push(`/records/${selected.person_id}?search=${results.id}`);
    },
    Escape: () => router.push("/search"),
  });

  async function attach() {
    try {
      await post(`/search/${results.id}/attach`);
      await reload();
    } catch (e) {
      setAttachError(messageOf(e));
    }
  }

  const face = results.face_index != null ? results.faces[results.face_index] : undefined;
  const warning = results.low_quality ? qualityWarning(face) : null;
  const floorLabel = offset > 0 ? "the raised floor" : "the display floor";
  const calibratedFloor = results.floor ?? 0;

  const row = (person: MatchPerson, index: number) => (
    <MatchRow
      key={person.person_id}
      person={person}
      results={results}
      face={face}
      selected={person === selected}
      onSelect={() => setCursor(index)}
    />
  );

  return (
    <div className={styles.sheet}>
      <aside className={styles.probe}>
        <h2 className={styles.panelTitle}>Probe photo</h2>
        <ProbePhoto results={results} face={face} />
        <dl className={styles.facts}>
          <dt>Case</dt>
          <dd>{results.case_number}</dd>
          <dt>Quality</dt>
          <dd>{results.low_quality ? "LOW QUALITY PROBE" : "OK"}</dd>
          <dt>Officer</dt>
          <dd>{results.officer}</dd>
          <dt>Run</dt>
          <dd>{stamp(results.created_at)}</dd>
          <dt>Searched</dt>
          <dd>{count(results.gallery_size ?? 0)} images</dd>
          <dt>Took</dt>
          <dd>{results.duration_ms ?? 0} ms</dd>
          <dt>Calibration</dt>
          <dd>V{results.calibration_version}</dd>
        </dl>
        <div className={styles.floor}>
          <span className="label">Floor</span>
          <span className={styles.floorValue}>{offset > 0 ? "Raised" : "Calibrated"}</span>
          <Button
            size="small"
            aria-label="Lower the floor"
            disabled={offset <= 0}
            onClick={() => setOffset((o) => Math.max(0, o - FLOOR_STEP))}
          >
            -
          </Button>
          <Button
            size="small"
            aria-label="Raise the floor"
            disabled={calibratedFloor + offset >= 0.99}
            onClick={() => setOffset((o) => o + FLOOR_STEP)}
          >
            +
          </Button>
        </div>
        {results.purged ? null : results.attached_to_case ? (
          <p className="label">Attached to case</p>
        ) : (
          <Button size="small" onClick={() => void attach()}>
            Attach to case
          </Button>
        )}
        {attachError ? <p role="alert">{attachError}</p> : null}
      </aside>

      <section className={styles.list} aria-label="Ranked matches">
        <header className={styles.listHead}>
          <h2 className={styles.panelTitle}>Possible matches</h2>
          <p className="muted">
            {visible.length} {visible.length === 1 ? "person" : "people"} above {floorLabel}
          </p>
        </header>
        {results.calibration_synthetic ? (
          <p className={styles.warning}>
            <strong>Synthetic calibration.</strong> Fitted on test data. These numbers mean nothing for real casework.
          </p>
        ) : null}
        {results.gallery_drifted ? (
          <p className={styles.warning}>
            <strong>Gallery size changed.</strong> It has moved more than 2x since calibration, so these bands are out of date.
          </p>
        ) : null}
        {warning ? (
          <p className={styles.warning}>
            <strong>LOW QUALITY PROBE.</strong> {warning}
          </p>
        ) : null}

        {visible.length === 0 ? (
          <div className={styles.none}>
            <p className={styles.noneTitle}>NO MATCH</p>
            <p>No record above {floorLabel}. Try a sharper, front-facing photo.</p>
            <Link href="/search" className={styles.open}>
              <Button as="span" variant="solid">
                New search
              </Button>
            </Link>
          </div>
        ) : (
          <ol className={styles.rows}>
            {leads.map(row)}
            {weak.length > 0 ? (
              <li className={styles.weakRow}>
                <span className="label">WEAK</span>
                <span className="muted">{weak.length} more</span>
                <Button size="small" aria-expanded={showWeak} onClick={() => setShowWeak((s) => !s)}>
                  {showWeak ? "Hide" : "Show"}
                </Button>
              </li>
            ) : null}
            {showWeak ? weak.map((person, index) => row(person, leads.length + index)) : null}
          </ol>
        )}
      </section>
    </div>
  );
}

export default function ResultsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { data: results, error, reload } = useResource<Results>(`/search/${id}`);

  return (
    <>
      <p className={styles.lead} role="note">
        {LEAD_NOTICE}
      </p>
      {error ? <FormError>{error}</FormError> : null}
      {results ? <Sheet results={results} reload={reload} /> : null}
      {!results && !error ? <Loading what="Opening results" /> : null}
    </>
  );
}
