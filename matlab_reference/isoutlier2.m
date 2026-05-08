function tf = isoutlier2(data)

[~,col] = size(data);
idx = 1:col;

tf_all = isoutlier(data,'gesd');
tf_all(1,idx) = false;

tf_row = isoutlier(data,'gesd',2);

tf = tf_all |tf_row;

end