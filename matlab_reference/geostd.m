function gsd = geostd(x)
%% Variables
n = length(x);
arr = zeros(n,1);
syms k;

%% Calculation
% Geometric Mean
gmean = geomean(x);

% Geometric Standard Deviation
for idx = 1:n
    arr(idx) = log(x(idx) / gmean) ^ 2;
end
gsd = sqrt(sum(arr) / n);

end