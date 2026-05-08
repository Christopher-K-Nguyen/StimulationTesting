function [FilesDir,isQuit] = getFiles(ext,isGetAll)
%% Variables
isQuit = false;
if isempty(ext)
    ext = '*';
else
    ext = lower(ext);
end
if iscell(ext)
    numOfExt = length(ext);
    ext_use = cell(1,numOfExt);
    ext_filt = '';
    for idx = 1:numOfExt
        ext_val = ext{idx};
        if ~startsWith(ext_val,'.')
            ext_new = ['*.' ext_val];
            ext_filt_alloc = [ext_filt ';' ext_new];
        else
            ext_new = ['*' ext_val];
            ext_filt_alloc = [ext_filt ';' ext_new];
        end
        ext_use{idx} = ext_new;
        ext_filt = ext_filt_alloc;
    end
    if startsWith(ext_filt,';')
        ext_filt = ext_filt(2:end);
    end
else
    numOfExt = 1;
    if ~startsWith(ext,'.')
        ext_filt = ['*.' ext];
    else
        ext_filt = ['*' ext];
    end
    ext_use = ext_filt;
end

%% Function
if isGetAll
    filepath = uigetdir(pwd, ...
        'Select folder');
else
    [selectFiles,filepath] = uigetfile(ext_filt, ...
        'Select files', ...
        'MultiSelect','on');
end

FilesDir = [];
if filepath == 0
    isQuit = true;
else
    if isGetAll
        FilesDir = dir(filepath);               % add files to directory
    else
        if numOfExt > 1
            for idx = 1:numOfExt
                ext_idx = ext_use{idx};
                files = fullfile(filepath,ext_idx);
                files_dir = dir(files);
                FilesDir_alloc = [FilesDir;files_dir];
                FilesDir = FilesDir_alloc;
            end
        else
            files = fullfile(filepath,ext_use);
            FilesDir = dir(files);
        end
        filename_cell = {FilesDir(:).name};
        files_tf = contains(filename_cell,selectFiles);
        FilesDir = FilesDir(files_tf);
    end
end

end