function [hasPat,idx] = contains3(str,pat)

hasContains = contains(str,pat,'IgnoreCase',true);
hasPat = any(hasContains);
idx = find(hasContains);

end