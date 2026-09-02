# Client Brief: JabRef

## Problem statement
Researchers, students, and technical writers need a reliable way to collect, organize, and cite scholarly references across the full research workflow. Existing approaches are often fragmented across websites, PDFs, writing tools, and personal folders, making it hard to keep bibliographic data accurate, deduplicate records, attach full texts, and format citations consistently. The team has been asked to build a free, open reference manager that helps users stay on top of literature from discovery through citation and sharing.

## Primary users / personas
- **Graduate student researcher** — collects papers from many sources, annotates reading progress, and needs fast citation insertion while writing.
- **Academic author / scientist** — maintains large literature libraries, requires clean metadata, duplicate detection, and discipline-specific citation workflows.
- **Research team member / lab coordinator** — shares curated reference libraries with collaborators and needs low-friction synchronization and common conventions.
- **Technical writer / LaTeX-heavy user** — depends on structured bibliographic data, stable citation keys, and export into publication-ready formats.

## Goals & desired outcomes
- Enable users to collect references quickly from online sources, identifiers, files, and browser-based discovery.
- Help users maintain high-quality, well-structured bibliographic libraries with minimal manual cleanup.
- Support end-to-end citation workflows for academic writing, especially for bibliography-centered authoring practices.
- Make attached documents and reference metadata easy to organize, search, and retrieve.
- Allow individuals and teams to share libraries without locking data into a proprietary ecosystem.
- Provide a free, community-friendly product that can be used across major desktop environments.

## Key features (capability-level, not technical)
- **Reference acquisition from multiple channels** — search scholarly sources, import common reference files, and create entries from standard identifiers.
- **Automatic metadata enrichment** — retrieve missing bibliographic fields and extract metadata from associated documents where possible.
- **Full-text attachment management** — link related files to references and support rule-based naming and organization of those documents.
- **Library organization tools** — group items into collections, tags, keyword-based sets, and saved searches, including hierarchical organization.
- **Advanced search, filtering, and discovery** — help users quickly find papers, narrow large libraries, and surface relevant literature.
- **Data quality management** — detect duplicates, compare records against trusted catalog sources, and support merge/correction workflows.
- **Flexible bibliographic modeling** — support common scholarly reference types and allow customization of fields, types, and citation-key rules.
- **Citation and bibliography output** — generate formatted citations/bibliographies in many styles and support cite-as-you-write workflows with external writing tools.
- **Import/export and interoperability** — read and write widely used scholarly formats so users can move data between tools and publishing workflows.
- **Collaboration and capture extensions** — support shared-library workflows and quick capture of references from the browser or automated interfaces.

## Quality attributes / non-functional needs
- **Cross-platform desktop usability** — consistent experience across major operating systems used by researchers.
- **Data portability and no vendor lock-in** — users must be able to store, inspect, back up, version, and share their library data easily.
- **Privacy-conscious operation** — users should retain control over their libraries and attachments, with clear boundaries around external lookups.
- **Offline-friendly core workflow** — organizing, editing, searching, and citing from an existing library should work without continuous internet access.
- **Scalability for large libraries** — responsive performance with substantial collections of references and attached files.
- **Extensibility and community sustainability** — the product should be adaptable by contributors for new formats, sources, and workflows over time.

## Constraints & assumptions
- The product must be released as free and open-source software for a broad academic audience.
- The solution should prioritize researcher workflows over general-purpose note-taking or document authoring use cases.
- Domain support for bibliography-centric formats and citation practices is a core requirement, not an optional add-on.
- User data should remain easy to share and archive using open, human-readable representations where practical.
- The team should assume integration with external scholarly catalogs, writing tools, and file systems will be important to adoption.

## Out of scope
- Building a full academic writing or word-processing application.
- Hosting or operating a proprietary cloud research platform or social network for scholars.
- Replacing publisher databases, institutional repositories, or full scholarly search engines.
- Providing peer review, manuscript submission, or research project management functionality.