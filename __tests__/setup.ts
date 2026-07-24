// jsdom doesn't implement scrollIntoView (used by the activity log autoscroll)
Element.prototype.scrollIntoView = () => {};
