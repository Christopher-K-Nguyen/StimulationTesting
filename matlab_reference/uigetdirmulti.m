function varargout = uigetdirmulti()

selpath = uigetdir(pwd,'Select folder containg sub-folders of interest');
List = dir(selpath);

% Remove blank indices
name_cell = {List(:).name};
empty_tf = contains(name_cell,{'.','..'});

% Remove file indices
bytes_arr = [List(:).bytes];
file_tf = bytes_arr ~= 0;

% Remove indices
remove_tf = empty_tf | file_tf;
List(remove_tf) = [];
name_cell = {List(:).name};

% Pattern
key = input('Enter folder pattern for specific folders (use comma for multiple), otherwise empty: ','s');
if ~isempty(key)
    key_cell = strsplit(key,{', ',',',' '});
    pattern_tf = contains(name_cell,key_cell, ...
        'IgnoreCase',true);
    List(~pattern_tf) = [];
    name_cell = {List(:).name}';
    folder_cell = {List(:).folder}';
end

numOfFolders = length(List);
selpath_multi = cell(numOfFolders,1);
for folderNum = 1:numOfFolders
    name = name_cell{folderNum};
    folder = folder_cell{folderNum};
    selpath_multi{folderNum} = fullfile(folder,name);
end

varargout{1} = selpath_multi;
varargout{2} = name_cell;

end
