// Post page-related code.
import u from './Util';
import _ from './utils/I18n';


u.addEventForChild(document, 'click', '.close-report', function (e, qelem) {
  const errorbox = document.querySelector('.error');

  let action = qelem.getAttribute('data-action');
  console.log('ACTION:' + action)
  let id = qelem.getAttribute('data-id');
  let type = qelem.getAttribute('data-type');

  if (type == "comment") {
    let uri = '/do/report/close_comment_report/' + id + '/' + action;
    u.post(uri, {},
      function (data) {
          if (data.status != "ok") {
            errorbox.style.display = 'block';
            errorbox.innerHTML = _('Error:') + data.error;
          } else {
              window.location.reload();
          }
      }, function () {
          errorbox.style.display = 'block';
          errorbox.innerHTML = _('Could not contact the server');
      });
  }
  else {
    let uri = '/do/report/close_post_report/' + id + '/' + action;
    u.post(uri, {},
      function (data) {
          if (data.status != "ok") {
            errorbox.style.display = 'block';
            errorbox.innerHTML = _('Error:') + data.error;
          } else {
              window.location.reload();
          }
      }, function () {
          errorbox.style.display = 'block';
          errorbox.innerHTML = _('Could not contact the server');
      });
    }
});
