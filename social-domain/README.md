# Tools and scripts for collecting social domain data

## Get screenshots for sampled data

N.B. This code is a working progress for code to obtain screenshots of
the sampled data for the social domain (from Mastodon). It will
certainly need to be updated over time to include other
functionalities and to account for particular instances of the
data. All outputs should be manually checked before being included in
any test sets, and some steps may still need to be manually carried
out (see below).

Screenshots are collected once the data has been downloaded and
sampled.

To get a few examples of data for demo purposes (N.B. taken from
official Mastodon accounts and not corresponding to the data from the
test sets):

```
python get_demo_data_from_json.py demo-data.json sampled-data-demo
```

Then to get the anonymised individual screenshots of the posts:

```
python get_screenshots.py sampled-data-demo/en --lang en --anonymise
```

Known issues and potential issues:

- The code expands hidden text and images, but is designed to hide alt
  text, which could cover the images included. However, alt text can
  still be visible if it is included directly in an image (i.e. it is not an
  element that can be dynamically hidden or shown, as is sometimes the
  case).

- The main post is anonymised for the username and account. However
  any posts that are quoted in the post will not be anonymised. They
  are often displayed via preview images that would have to be
  modified directly (so this needs to be manually done for now).

- The account name can be formatted, and this formatting can be
  included in the name of the account when looking at the underlying
  data (e.g. bold font = username :bc:). This is currently handled by
  the current code (by adding the bold face variant to the anonymisation map),
  but any other formatting types would need to be included should they
  appear. If not, the name will not be anonymised.
